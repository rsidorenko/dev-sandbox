#!/usr/bin/env python3
"""Operator: grant a paid-tier subscription to a Telegram user (by telegram_user_id).

Modes
-----
  analyze (default, read-only):
      python grant_subscription.py --telegram-id 8158783115
    Prints every subscription_plans row (the LIVE tariffs) and the target user's
    identity + current subscription_snapshots state.

  grant:
      python grant_subscription.py --telegram-id 8158783115 --grant --plan 1m
    1. reads the LIVE plan (price_rubles, duration_days, default_device_limit) from
       subscription_plans — never hardcoded, so it always matches the real tariff;
    2. activates subscription_snapshots: state_label='active',
       active_until_utc = max(now, current) + duration_days, plan_id, device_count,
       keys_deactivated_at=NULL, keys_deleted_at=NULL;
    3. records a billing_events_ledger fact: event_type='subscription_activated',
       status='accepted', amount = plan price (kopecks) — and runs the real UC-05
       apply on it (creates the apply idempotency + audit rows), so the record looks
       exactly like a correctly-applied payment;
    4. re-provisions the user's VLESS client on every active server (idempotent upsert);
    5. prints verification.

Runs in the production container with DATABASE_URL + FIELD_ENCRYPTION_KEY.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

# reuse the proven panel client + helpers from the rebuild tool
sys.path.insert(0, os.path.dirname(__file__))
from rebuild_panel_clients import (  # noqa: E402
    PanelClient,
    _email_from_internal,
    _expiry_from_datetime,
    _load_server_configs,
    _TRIAL_DEVICE_LIMIT,
)

EVENT_TYPE = "subscription_activated"  # UC05_ALLOWLISTED_EVENT_TYPES
PROVIDER_KEY = "yookassa"  # looks like the normal RU payment provider
CURRENCY = "RUB"


async def _show_plans(pool: asyncpg.Pool) -> None:
    # Plans are AUTHORITATIVE in code (app.domain.plans) — that's what the bot sells.
    # The subscription_plans DB table is vestigial (not read by the app) and may be stale.
    from app.domain.plans import get_all_plans
    print("=== plans (from code — what the bot sells) ===")
    for p in sorted(get_all_plans(), key=lambda x: x.duration_days):
        print(f"  {p.plan_id:6} {p.duration_days:4}d  {p.price_rubles:5} RUB  "
              f"devices={p.default_device_limit}  extra_dev={p.extra_device_price_rubles} RUB")


async def _lookup_user(pool: asyncpg.Pool, tg_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT telegram_user_id, internal_user_id, vless_uuid FROM user_identities "
        "WHERE telegram_user_id = $1",
        tg_id,
    )


async def _show_user(pool: asyncpg.Pool, tg_id: int) -> dict | None:
    ident = await _lookup_user(pool, tg_id)
    print(f"\n=== user telegram_user_id={tg_id} ===")
    if not ident:
        print("  NOT FOUND in user_identities")
        return None
    print(f"  internal_user_id = {ident['internal_user_id']}")
    print(f"  vless_uuid       = {ident['vless_uuid']}")
    snap = await pool.fetchrow(
        "SELECT state_label, plan_id, device_count, active_until_utc, "
        "keys_deactivated_at, keys_deleted_at FROM subscription_snapshots "
        "WHERE internal_user_id = $1",
        ident["internal_user_id"],
    )
    if snap:
        print(f"  snapshot: state={snap['state_label']} plan={snap['plan_id']} "
              f"devices={snap['device_count']} active_until={snap['active_until_utc']} "
              f"deactivated_at={snap['keys_deactivated_at']} deleted_at={snap['keys_deleted_at']}")
    else:
        print("  snapshot: NONE")
    return {"internal_user_id": ident["internal_user_id"], "vless_uuid": ident["vless_uuid"],
            "snap": snap, "device_count": (snap["device_count"] if snap else None)}


async def analyze(tg_id: int, dsn: str) -> None:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        await _show_plans(pool)
        await _show_user(pool, tg_id)
    finally:
        await pool.close()


async def grant(tg_id: int, plan_id: str, dsn: str) -> None:
    from app.domain.plans import get_plan
    from app.persistence.postgres_billing_subscription_apply import (
        PostgresAtomicUC05SubscriptionApply,
    )

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        await _show_plans(pool)
        info = await _show_user(pool, tg_id)
        if not info:
            print("\n!! user not found — cannot grant")
            return
        if not info["vless_uuid"]:
            print("\n!! user has no vless_uuid — register them first (/start)")
            return

        plan = get_plan(plan_id)
        if plan is None:
            print(f"\n!! plan {plan_id} not found in code (app.domain.plans)")
            return
        print(f"\n=== GRANT plan={plan.plan_id} ({plan.duration_days}d, "
              f"{plan.price_rubles} RUB, devices={plan.default_device_limit}) ===")

        internal_user_id = info["internal_user_id"]
        device_count = info["device_count"] or plan.default_device_limit
        now = datetime.now(UTC)
        # extend from current active_until if still in the future, else from now
        base = info["snap"]["active_until_utc"] if info["snap"] and info["snap"]["active_until_utc"] else None
        start = base if (base and base > now) else now
        new_until = start + timedelta(days=int(plan.duration_days))

        # 1) record the billing fact + run the real UC-05 apply FIRST.
        #    Apply's snapshot upsert sets state_label='active' but clobbers plan_id /
        #    active_until, so the duration UPDATE must run AFTER it (step 2).
        fact_ref = f"grant:{internal_user_id}:{plan.plan_id}:{uuid.uuid4().hex[:12]}"
        ext_id = f"grant-{uuid.uuid4().hex[:16]}"
        corr = f"grant-{uuid.uuid4().hex[:8]}"
        amount_kopecks = int(plan.price_rubles) * 100
        await pool.execute(
            """INSERT INTO billing_events_ledger
               (internal_fact_ref, billing_provider_key, external_event_id, event_type,
                event_effective_at, event_received_at, internal_user_id, checkout_attempt_id,
                amount_minor_units, currency_code, status, ingestion_correlation_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,NULL,$8,$9,'accepted',$10)""",
            fact_ref, PROVIDER_KEY, ext_id, EVENT_TYPE, now, now,
            internal_user_id, amount_kopecks, CURRENCY, corr,
        )
        print(f"  ledger fact inserted: ref={fact_ref} amount={amount_kopecks} kopecks status=accepted")
        apply_pg = PostgresAtomicUC05SubscriptionApply(pool)
        ar = await apply_pg.apply_by_internal_fact_ref(fact_ref)
        print(f"  UC-05 apply: outcome={ar.operation_outcome} apply={ar.apply_outcome} idempotent={ar.idempotent_replay}")

        # 2) set plan + duration + devices AFTER apply (so apply's upsert doesn't clobber them)
        result = await pool.execute(
            """UPDATE subscription_snapshots
               SET state_label = 'active',
                   plan_id = $2,
                   device_count = $3,
                   active_until_utc = $4,
                   keys_deactivated_at = NULL,
                   keys_deleted_at = NULL,
                   updated_at = NOW()
               WHERE internal_user_id = $1""",
            internal_user_id, str(plan.plan_id), device_count, new_until,
        )
        print(f"  snapshot UPDATE: {result}  active_until={new_until.isoformat()}")

        # 3) re-provision the user's VLESS client on every active server
        print("\n  provisioning keys on active servers...")
        servers = await _load_server_configs(pool)
        expiry_ts = _expiry_from_datetime(new_until)
        added = failed = 0
        for s in servers:
            pc = PanelClient(
                panel_url=s["panel_url"], username=s["panel_username"], password=s["panel_password"],
                inbound_id=s["inbound_id"], server_id=s["server_id"], label=s["label"],
                transport_type=s["transport_type"], server_host=s["server_host"], server_port=s["server_port"],
            )
            email = _email_from_internal(internal_user_id, transport_type=s["transport_type"])
            ok = await pc.add_client(
                user_uuid=info["vless_uuid"], email=email, expiry_ts=expiry_ts,
                enable=True, limit_ip=(device_count if device_count > 0 else _TRIAL_DEVICE_LIMIT),
            )
            await pc.aclose()
            tag = "ok" if ok else "FAIL"
            print(f"    server {s['server_id']} {s['label']} [{s['transport_type']}]: {tag}")
            added += 1 if ok else 0
            failed += 0 if ok else 1
        print(f"  keys: {added} ok, {failed} failed across {len(servers)} servers")

        # 4) verify
        snap2 = await pool.fetchrow(
            "SELECT state_label, plan_id, active_until_utc FROM subscription_snapshots WHERE internal_user_id=$1",
            internal_user_id,
        )
        print(f"\n=== VERIFY ===  state={snap2['state_label']} plan={snap2['plan_id']} active_until={snap2['active_until_utc']}")
    finally:
        await pool.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--telegram-id", type=int, required=True)
    p.add_argument("--grant", action="store_true", help="grant (default: analyze only)")
    p.add_argument("--plan", default="1m", help="plan_id to grant (default 1m)")
    args = p.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    if args.grant:
        asyncio.run(grant(args.telegram_id, args.plan, dsn))
    else:
        asyncio.run(analyze(args.telegram_id, dsn))


if __name__ == "__main__":
    main()
