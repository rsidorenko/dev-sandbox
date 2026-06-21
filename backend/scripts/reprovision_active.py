#!/usr/bin/env python3
"""Operator: re-provision EVERY active user's VLESS keys on all active servers.

Cures the desync where a renewed subscription's `active_until` was updated in the
DB but NOT pushed to the 3x-ui panels — so when the stale panel expiryTime passed,
x-ui auto-disabled the client (`enable=false` in client_traffics) and the user's
key stopped working despite an active subscription. (Helsinki-desync class.)

This is desired-state reconciliation: for each active, non-deleted user, force an
idempotent upsert on every active server's inbound with:
  - the derived per-transport VLESS UUID (matches what /sub/ links use)
  - enable=True
  - expiryTime = the user's current snapshot active_until
  - limitIp = device_count
It does NOT change plan_id, does NOT extend/shorten active_until, does NOT insert
billing facts. Pure key-state repair.

Intended to run BOTH on demand (relay-setup `reprovision`) and on a schedule
(reprovision-active.yml, ~every 10 min) so desyncs auto-heal before users notice.

Runs in the production container with DATABASE_URL + FIELD_ENCRYPTION_KEY.
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

# reuse the proven panel client + helpers (same as grant_subscription.py)
sys.path.insert(0, os.path.dirname(__file__))
from rebuild_panel_clients import (  # noqa: E402
    PanelClient,
    _email_from_internal,
    _expiry_from_datetime,
    _load_server_configs,
    _TRIAL_DEVICE_LIMIT,
    _vless_uuid_for_transport,
)


async def run() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    try:
        users = await pool.fetch(
            """SELECT i.internal_user_id, i.telegram_user_id,
                      s.active_until_utc, s.device_count
               FROM user_identities i
               JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id
               WHERE s.state_label = 'active'
                 AND s.keys_deleted_at IS NULL
               ORDER BY i.internal_user_id"""
        )
        servers = await _load_server_configs(pool)
        print(f"active users={len(users)}  active servers={len(servers)}")
        if not users or not servers:
            print("nothing to do")
            return

        grand_ok = grand_fail = 0
        fail_users = 0
        for u in users:
            uid = u["internal_user_id"]
            exp_ts = _expiry_from_datetime(u["active_until_utc"])
            dev = u["device_count"] or 0
            limit_ip = dev if dev > 0 else _TRIAL_DEVICE_LIMIT
            u_ok = u_fail = 0
            for s in servers:
                pc = PanelClient(
                    panel_url=s["panel_url"], username=s["panel_username"], password=s["panel_password"],
                    inbound_id=s["inbound_id"], server_id=s["server_id"], label=s["label"],
                    transport_type=s["transport_type"], server_host=s["server_host"], server_port=s["server_port"],
                )
                email = _email_from_internal(uid, transport_type=s["transport_type"])
                try:
                    ok = await pc.add_client(
                        user_uuid=_vless_uuid_for_transport(uid, s["transport_type"]),
                        email=email, expiry_ts=exp_ts,
                        enable=True, limit_ip=limit_ip,
                    )
                except Exception as exc:  # noqa: BLE001 — keep going across users/servers
                    print(f"    !! {uid} server {s['server_id']} {s['label']}: {exc}")
                    ok = False
                await pc.aclose()
                if ok:
                    u_ok += 1
                else:
                    u_fail += 1
            grand_ok += u_ok
            grand_fail += u_fail
            if u_fail:
                fail_users += 1
                print(f"  {uid} (tg={u['telegram_user_id']}): {u_ok} ok, {u_fail} FAIL")
        print(f"\nDONE: {grand_ok} ok, {grand_fail} fail across {len(users)} users × {len(servers)} servers"
              f"  ({fail_users} users had >=1 failure)")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
