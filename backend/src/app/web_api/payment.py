"""Web API payment endpoints — create YooKassa payment session, check status."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.application.interfaces import SubscriptionSnapshot
from app.domain.devices import DEFAULT_DEVICE_LIMIT, extra_device_cost
from app.domain.plans import calculate_total_price, calculate_total_price_kopecks, get_plan
from app.domain.referral import build_commissions_for_payment, resolve_direct_and_indirect_referrers
from app.persistence.postgres_referral import (
    PostgresReferralBalanceRepository,
    PostgresReferralRelationshipRepository,
    PostgresReferralTransactionRepository,
)
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.referral_contracts import ReferralTransactionRecord
from app.runtime.fulfillment_processor import (
    CreditedCommission,
    _notify_referrers_of_commission_best_effort,
)
from app.web_api.helpers import safe_json_error, truthy
from app.web_api.middleware import require_auth

_LOGGER = logging.getLogger(__name__)


class _InsufficientBalance(Exception):
    """Internal control-flow signal: debit refused (balance < amount). Raised inside
    the payment transaction so the atomic audit-insert rolls back with the debit."""


ENV_YOOKASSA_ENABLED = "YOOKASSA_ENABLED"
ENV_RETURN_URL = "NEXT_PUBLIC_SITE_URL"


async def handle_create_payment(request: Request) -> JSONResponse:
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    telegram_user_id = auth_result.get("telegram_user_id")
    if telegram_user_id is None:
        return safe_json_error(403, "no_telegram_identity")

    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request")

    plan_id = data.get("plan_id", "").strip()
    device_count_raw = data.get("device_count", 5)
    try:
        device_count = int(device_count_raw)
    except (ValueError, TypeError):
        device_count = 5

    plan = get_plan(plan_id)
    if plan is None:
        return safe_json_error(400, "invalid_plan_id")

    total_rubles = calculate_total_price(plan, device_count)
    total_kopecks = total_rubles * 100

    yookassa_enabled = truthy(os.environ.get(ENV_YOOKASSA_ENABLED))

    if not yookassa_enabled:
        return JSONResponse(
            {
                "ok": True,
                "status": "payment_unavailable",
                "plan_id": plan_id,
                "plan_name": plan_display_name_safe(plan_id),
                "device_count": device_count,
                "amount_rubles": total_rubles,
                "amount_kopecks": total_kopecks,
                "message": (
                    "Для оформления подписки нажмите кнопку ниже. "
                    "После подтверждения оплаты доступ к сервису будет активирован автоматически."
                ),
            }
        )

    return_url = os.environ.get(ENV_RETURN_URL, "").strip()
    if not return_url:
        return_url = "https://bravada-connect.ru"
    return_url = f"{return_url.rstrip('/')}/dashboard"

    from app.security.checkout_reference import create_signed_checkout_reference
    from app.yookassa.client import YooKassaClient

    client = YooKassaClient.from_env()
    if client is None:
        _LOGGER.error("yookassa client not configured")
        return safe_json_error(503, "payment_provider_not_configured")

    # Resolve the real internal_user_id from the identity row. Web-only (email)
    # accounts carry a NON-POSITIVE telegram_user_id and an internal_user_id of
    # "web_<hash>" (NOT f"u{telegram_user_id}"); the YooKassa webhook must apply
    # the subscription to that exact identity, so we carry it in the payment
    # metadata. Mirrors handle_get_keys / handle_activate_trial.
    pool = request.app.state.pool
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return safe_json_error(404, "identity_not_found")
    internal_user_id = identity["internal_user_id"]

    checkout_secret = (
        os.environ.get("CHECKOUT_REFERENCE_SECRET") or os.environ.get("TELEGRAM_CHECKOUT_REFERENCE_SECRET", "").strip()
    )
    # Always carry the real identity so the webhook credits the correct account.
    payment_metadata: dict[str, str] = {"internal_user_id": internal_user_id}
    # Checkout-reference correlation assumes a real Telegram identity —
    # validate_telegram_user_id rejects the non-positive ids used by web-only
    # accounts (which previously raised here, outside any try/except, and crashed
    # the request with a 500). The reference is also not consumed by the YooKassa
    # webhook path, so skip it for web-only users; they are correlated by
    # metadata.internal_user_id instead.
    if checkout_secret and isinstance(telegram_user_id, int) and telegram_user_id > 0:
        signed = create_signed_checkout_reference(
            telegram_user_id=telegram_user_id,
            internal_user_id=internal_user_id,
            secret=checkout_secret,
        )
        payment_metadata["client_reference_id"] = signed.reference_id
        payment_metadata["client_reference_proof"] = signed.reference_proof

    try:
        result = await client.create_payment(
            amount_rubles=total_rubles,
            plan_id=plan_id,
            device_count=device_count,
            telegram_user_id=telegram_user_id,
            return_url=return_url,
            description=f"Bravada VPN — {plan_display_name_safe(plan_id)}",
            metadata=payment_metadata,
        )
    except Exception:
        _LOGGER.exception("yookassa create_payment failed")
        return safe_json_error(502, "payment_creation_failed")

    return JSONResponse(
        {
            "ok": True,
            "status": "pending",
            "plan_id": plan_id,
            "plan_name": plan_display_name_safe(plan_id),
            "device_count": device_count,
            "amount_rubles": total_rubles,
            "amount_kopecks": total_kopecks,
            "payment_url": result.confirmation_url,
            "payment_id": result.payment_id,
        }
    )


async def handle_get_payment_status(request: Request) -> JSONResponse:
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    payment_id = request.path_params.get("payment_id", "")
    if not payment_id:
        return safe_json_error(400, "missing_payment_id")

    from app.yookassa.client import YooKassaClient

    client = YooKassaClient.from_env()
    if client is None:
        return JSONResponse({"ok": True, "payment_id": payment_id, "status": "unknown"})

    try:
        info = await client.get_payment(payment_id)
    except Exception:
        _LOGGER.warning("yookassa get_payment failed id=%s", payment_id, exc_info=True)
        return JSONResponse({"ok": True, "payment_id": payment_id, "status": "unknown"})

    if info is None:
        return JSONResponse({"ok": True, "payment_id": payment_id, "status": "not_found"})

    return JSONResponse(
        {
            "ok": True,
            "payment_id": payment_id,
            "status": info.status,
            "amount": info.amount_value,
            "metadata": info.metadata,
        }
    )


def plan_display_name_safe(plan_id: str) -> str:
    from app.domain.plans import plan_display_name

    return plan_display_name(plan_id)


async def handle_create_add_device_payment(request: Request) -> JSONResponse:
    """Create a YooKassa payment to ADD devices to an existing active subscription.

    Unlike ``/payment/create`` (a full plan purchase + renewal), this charges only
    for the extra devices (delta x daily device price x plan duration) and does NOT
    extend the subscription. The payment carries ``kind=add_device`` so the YooKassa
    webhook's add-device path raises ``device_count`` without touching
    ``active_until`` — consistent with the bot's add-device flow.
    """
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result
    telegram_user_id = auth_result.get("telegram_user_id")
    if telegram_user_id is None:
        return safe_json_error(403, "no_telegram_identity")

    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request")
    try:
        new_device_count = int(data.get("device_count", 0))
    except (ValueError, TypeError):
        return safe_json_error(400, "invalid_device_count")
    if new_device_count < 1 or new_device_count > 20:
        return safe_json_error(400, "invalid_device_count")

    pool = request.app.state.pool
    internal_user_id = f"u{telegram_user_id}"
    snap = await pool.fetchrow(
        "SELECT device_count, plan_id, state_label FROM subscription_snapshots WHERE internal_user_id = $1",
        internal_user_id,
    )
    if snap is None or snap["state_label"] != "active":
        return safe_json_error(409, "no_active_subscription")
    current = snap["device_count"] or 5
    if new_device_count <= current:
        return safe_json_error(400, "device_count_not_higher")

    plan_id = snap["plan_id"] or "1m"
    plan = get_plan(plan_id)
    if plan is None:
        return safe_json_error(400, "invalid_plan")
    cost_rubles = extra_device_cost(new_device_count, current, plan.duration_days)
    if cost_rubles <= 0:
        return safe_json_error(400, "device_count_not_higher")
    cost_kopecks = cost_rubles * 100

    if not truthy(os.environ.get(ENV_YOOKASSA_ENABLED)):
        return safe_json_error(503, "payment_unavailable")

    return_url = os.environ.get(ENV_RETURN_URL, "https://bravada-connect.ru").strip().rstrip("/")
    from app.yookassa.client import YooKassaClient

    client = YooKassaClient.from_env()
    if client is None:
        _LOGGER.error("yookassa client not configured (add-device)")
        return safe_json_error(503, "payment_provider_not_configured")
    try:
        result = await client.create_payment(
            amount_rubles=cost_rubles,
            plan_id=plan_id,
            device_count=new_device_count,
            telegram_user_id=telegram_user_id,
            return_url=f"{return_url}/dashboard",
            description=f"Bravada VPN — добавление устройств ({current}→{new_device_count})",
            metadata={
                "kind": "add_device",
                "new_device_count": str(new_device_count),
                "expected_amount_kopecks": str(cost_kopecks),
            },
        )
    except Exception:
        _LOGGER.exception("yookassa create_payment failed (add-device)")
        return safe_json_error(502, "payment_creation_failed")

    return JSONResponse(
        {
            "ok": True,
            "status": "pending",
            "device_count": new_device_count,
            "amount_rubles": cost_rubles,
            "amount_kopecks": cost_kopecks,
            "payment_url": result.confirmation_url,
            "payment_id": result.payment_id,
        }
    )


async def handle_create_balance_payment(request: Request) -> JSONResponse:
    """Pay for a subscription from the referral balance (web equivalent of the bot's
    ``_process_balance_payment``). Atomic debit + snapshot upsert + VLESS keys +
    referral commissions, with 3-layer idempotency:

    1. ``X-Idempotency-Key`` header → ``correlation_id`` baked into the audit-row
       ``description``; UNIQUE(internal_user_id, description) (migration 048) dedups
       a replayed key → ``already_completed`` without re-debiting.
    2. ``append_transaction_if_description_absent_in_connection`` runs FIRST inside
       the debit transaction, so insufficient balance / any failure rolls the audit
       row back too (no orphan 'debited without audit' gap, unlike the bot).
    3. ``pg_try_advisory_xact_lock(hashtext(internal_user_id))`` serializes concurrent
       same-user requests across uvicorn workers (the bot's in-process dict is useless
       here) → 409 ``payment_in_progress``.
    """
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result
    telegram_user_id = auth_result.get("telegram_user_id")
    if telegram_user_id is None:
        return safe_json_error(403, "no_telegram_identity")

    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request")
    plan_id = str(data.get("plan_id", "")).strip()
    try:
        device_count = int(data.get("device_count", DEFAULT_DEVICE_LIMIT))
    except (ValueError, TypeError):
        return safe_json_error(400, "invalid_device_count")
    if device_count < DEFAULT_DEVICE_LIMIT or device_count > 20:
        return safe_json_error(400, "invalid_device_count")

    plan = get_plan(plan_id)
    if plan is None:
        return safe_json_error(400, "invalid_plan_id")
    total_kopecks = calculate_total_price_kopecks(plan, device_count)

    pool = request.app.state.pool
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return safe_json_error(404, "identity_not_found")
    internal_user_id = identity["internal_user_id"]

    correlation_id = (request.headers.get("X-Idempotency-Key", "") or "").strip() or f"web-bal-pay-{uuid.uuid4()}"

    vless_provider = getattr(request.app.state, "vless_provider", None)
    pre_payment_snap: SubscriptionSnapshot | None = None
    with contextlib.suppress(Exception):
        pre_payment_snap = await PostgresSubscriptionSnapshotReader(pool).get_for_user(internal_user_id)

    now = datetime.now(UTC)
    active_until: datetime | None = None
    balance_after: int = 0

    try:
        async with pool.acquire() as conn, conn.transaction():
            locked = await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext($1))", internal_user_id)
            if not locked:
                return safe_json_error(409, "payment_in_progress")

            description = f"web balance payment: {plan_id} x {device_count} devices [{correlation_id}]"
            inserted = (
                await PostgresReferralTransactionRepository.append_transaction_if_description_absent_in_connection(
                    conn,
                    ReferralTransactionRecord(
                        transaction_id=f"ref-{uuid.uuid4()}",
                        internal_user_id=internal_user_id,
                        amount_kopecks=-total_kopecks,
                        transaction_type="subscription_payment",
                        related_user_id=None,
                        related_plan_id=plan_id,
                        description=description,
                        created_at=now,
                    ),
                )
            )
            if not inserted:
                existing = await PostgresSubscriptionSnapshotReader.get_for_user_in_connection(conn, internal_user_id)
                bal = await PostgresReferralBalanceRepository.get_balance_in_connection(conn, internal_user_id)
                return JSONResponse(
                    {
                        "ok": True,
                        "status": "already_completed",
                        "plan_id": plan_id,
                        "plan_name": plan_display_name_safe(plan_id),
                        "device_count": device_count,
                        "amount_rubles": total_kopecks // 100,
                        "amount_kopecks": total_kopecks,
                        "active_until": (
                            existing.active_until_utc.date().isoformat()
                            if existing and existing.active_until_utc
                            else None
                        ),
                        "balance_rubles_remaining": round((bal.balance_kopecks if bal else 0) / 100, 2),
                    }
                )

            debit = await PostgresReferralBalanceRepository.debit_in_connection(conn, internal_user_id, total_kopecks)
            if debit is None:
                raise _InsufficientBalance()

            existing_snap = await PostgresSubscriptionSnapshotReader.get_for_user_in_connection(conn, internal_user_id)
            base_date = now
            if existing_snap and existing_snap.active_until_utc and existing_snap.active_until_utc > now:
                base_date = existing_snap.active_until_utc
            active_until = (base_date + timedelta(days=plan.duration_days)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            await PostgresSubscriptionSnapshotReader.upsert_state_in_connection(
                conn,
                SubscriptionSnapshot(
                    internal_user_id=internal_user_id,
                    state_label="active",
                    active_until_utc=active_until,
                    plan_id=plan_id,
                    device_count=device_count,
                ),
            )
            balance_after = debit.balance_kopecks
    except _InsufficientBalance:
        return safe_json_error(402, "insufficient_balance")
    except Exception:
        _LOGGER.exception("balance payment tx failed user=%s", internal_user_id)
        return safe_json_error(500, "balance_payment_failed")

    # VLESS keys (outside tx — network IO). Branch on the snapshot's keys lifecycle,
    # exactly like the bot _process_balance_payment: keys_deleted → create_user;
    # keys_deactivated (within grace) → activate_user (delete+re-add to clear
    # client_traffics.enable=false, which create_user alone leaves in place for the
    # same uuid); else → create_user. Compensate (refund + revert snapshot + drop the
    # audit row + write an offsetting refund row) on failure so a replay re-attempts.
    if vless_provider is not None:
        key_snap = None
        with contextlib.suppress(Exception):
            key_snap = await pool.fetchrow(
                "SELECT keys_deactivated_at, keys_deleted_at, device_count FROM subscription_snapshots"
                " WHERE internal_user_id=$1",
                internal_user_id,
            )
        keys_deleted = bool(key_snap and key_snap["keys_deleted_at"] is not None)
        keys_deactivated = bool(key_snap and key_snap["keys_deactivated_at"] is not None)

        vless_ok = False
        for attempt in range(3):
            try:
                if keys_deleted or not keys_deactivated:
                    await vless_provider.create_user(internal_user_id=internal_user_id, device_count=device_count)
                else:
                    await vless_provider.activate_user(internal_user_id=internal_user_id, device_count=device_count)
                vless_ok = True
                break
            except Exception:
                _LOGGER.warning("web balance vless retry user=%s attempt=%d/3", internal_user_id, attempt + 1)
                await asyncio.sleep(2.0)
        if not vless_ok:
            _LOGGER.error("web balance vless FAILED after retries — refunding user=%s", internal_user_id)
            # Atomic compensation: credit + snapshot revert + audit-row DELETE + refund
            # row in ONE transaction. Any failure rolls back ALL four — leaving the debit
            # audit row in place, so a replay returns already_completed (NO double-charge);
            # the 30-min reconcile/operator resolves the stuck state.
            try:
                async with pool.acquire() as conn, conn.transaction():
                    await PostgresReferralBalanceRepository.credit_in_connection(conn, internal_user_id, total_kopecks)
                    if pre_payment_snap is not None:
                        await PostgresSubscriptionSnapshotReader.upsert_state_in_connection(conn, pre_payment_snap)
                    else:
                        await conn.execute(
                            "UPDATE subscription_snapshots SET state_label='inactive',"
                            " active_until_utc=NULL, updated_at=NOW() WHERE internal_user_id=$1",
                            internal_user_id,
                        )
                    await conn.execute(
                        "DELETE FROM referral_transactions WHERE internal_user_id=$1 AND description=$2",
                        internal_user_id,
                        description,
                    )
                    await conn.execute(
                        "INSERT INTO referral_transactions"
                        " (transaction_id, internal_user_id, amount_kopecks, transaction_type,"
                        " related_user_id, related_plan_id, description, created_at)"
                        " VALUES ($1, $2, $3, 'subscription_payment', NULL, $4, $5, now())",
                        f"ref-{uuid.uuid4()}",
                        internal_user_id,
                        total_kopecks,
                        plan_id,
                        f"web balance refund (vless failed): {plan_id} x {device_count} devices [{correlation_id}]",
                    )
            except Exception:
                _LOGGER.error(
                    "web balance compensation tx FAILED user=%s cid=%s — debit left in place,"
                    " replay will return already_completed; manual reconcile needed",
                    internal_user_id,
                    correlation_id,
                    exc_info=True,
                )
            return safe_json_error(503, "vless_creation_failed")
        with contextlib.suppress(Exception):
            await pool.execute(
                "UPDATE subscription_snapshots SET keys_deactivated_at=NULL, keys_deleted_at=NULL,"
                " updated_at=NOW() WHERE internal_user_id=$1",
                internal_user_id,
            )

    # Referral commissions (best-effort) + notify TG referrers — parity with the
    # card-payment path (PR #468). A lost commission is a real incident, so log it
    # traceably instead of silently swallowing (regression vs PR #470).
    notifier = getattr(request.app.state, "activation_notifier", None)
    try:
        credited = await _credit_web_balance_commissions(pool, internal_user_id, plan_id, total_kopecks, correlation_id)
    except Exception:
        _LOGGER.warning(
            "web balance commission credit failed payer=%s cid=%s — referrer may lose commission",
            internal_user_id,
            correlation_id,
            exc_info=True,
        )
        credited = []
    if credited and notifier is not None:
        with contextlib.suppress(Exception):
            await _notify_referrers_of_commission_best_effort(
                pool=pool,
                notifier=notifier,
                credited=credited,
                correlation_id=correlation_id,
            )

    return JSONResponse(
        {
            "ok": True,
            "status": "completed",
            "plan_id": plan_id,
            "plan_name": plan_display_name_safe(plan_id),
            "device_count": device_count,
            "amount_rubles": total_kopecks // 100,
            "amount_kopecks": total_kopecks,
            "active_until": active_until.date().isoformat() if active_until else None,
            "balance_rubles_remaining": round(balance_after / 100, 2),
        }
    )


async def handle_create_add_device_balance_payment(request: Request) -> JSONResponse:
    """Pay to add devices to an active subscription from the referral balance (web
    equivalent of the bot's ``_process_add_device_balance``). Charges only the
    extra-device cost, sets ``device_count=new_count``, does NOT extend
    ``active_until``; no VLESS, no referral commission (parity with the bot).
    Same 3-layer idempotency as the plan-purchase handler."""
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result
    telegram_user_id = auth_result.get("telegram_user_id")
    if telegram_user_id is None:
        return safe_json_error(403, "no_telegram_identity")

    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request")
    try:
        new_device_count = int(data.get("device_count", 0))
    except (ValueError, TypeError):
        return safe_json_error(400, "invalid_device_count")
    if new_device_count < DEFAULT_DEVICE_LIMIT or new_device_count > 20:
        return safe_json_error(400, "invalid_device_count")

    pool = request.app.state.pool
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return safe_json_error(404, "identity_not_found")
    internal_user_id = identity["internal_user_id"]

    correlation_id = (request.headers.get("X-Idempotency-Key", "") or "").strip() or f"web-adddev-{uuid.uuid4()}"
    # Description is deterministic in the REQUEST alone (new_device_count + key, no
    # `current`) so a replayed X-Idempotency-Key matches even after device_count has
    # already been raised by the first call.
    description = f"web add device → {new_device_count} [{correlation_id}]"
    now = datetime.now(UTC)
    balance_after: int = 0
    cost_kopecks = 0
    cost_rubles = 0

    class _NoActive(Exception):
        pass

    class _NotHigher(Exception):
        pass

    try:
        async with pool.acquire() as conn, conn.transaction():
            locked = await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext($1))", internal_user_id)
            if not locked:
                return safe_json_error(409, "payment_in_progress")

            # Dedup FIRST (before the snapshot pre-check): a replayed key must
            # short-circuit here, otherwise a prior success (device_count already
            # raised) would re-fail `new <= current` and never reach dedup.
            already = await conn.fetchval(
                "SELECT 1 FROM referral_transactions WHERE internal_user_id=$1 AND description=$2",
                internal_user_id,
                description,
            )
            if already:
                bal = await PostgresReferralBalanceRepository.get_balance_in_connection(conn, internal_user_id)
                snap_now = await PostgresSubscriptionSnapshotReader.get_for_user_in_connection(conn, internal_user_id)
                return JSONResponse(
                    {
                        "ok": True,
                        "status": "already_completed",
                        "device_count": (snap_now.device_count if snap_now else new_device_count),
                        "amount_rubles": 0,
                        "amount_kopecks": 0,
                        "balance_rubles_remaining": round((bal.balance_kopecks if bal else 0) / 100, 2),
                    }
                )

            # Snapshot read INSIDE the tx (after the lock) — fresh `current`, no stale
            # overcharge and no overwrite of a concurrent commit.
            snap = await PostgresSubscriptionSnapshotReader.get_for_user_in_connection(conn, internal_user_id)
            if snap is None or snap.state_label != "active":
                raise _NoActive()
            current = snap.device_count or DEFAULT_DEVICE_LIMIT
            if new_device_count <= current:
                raise _NotHigher()
            plan_id = snap.plan_id or "1m"
            plan = get_plan(plan_id)
            if plan is None:
                raise _NoActive()
            cost_rubles = extra_device_cost(new_device_count, current, plan.duration_days)
            if cost_rubles <= 0:
                raise _NotHigher()
            cost_kopecks = cost_rubles * 100

            inserted = (
                await PostgresReferralTransactionRepository.append_transaction_if_description_absent_in_connection(
                    conn,
                    ReferralTransactionRecord(
                        transaction_id=f"ref-{uuid.uuid4()}",
                        internal_user_id=internal_user_id,
                        amount_kopecks=-cost_kopecks,
                        transaction_type="subscription_payment",
                        related_user_id=None,
                        related_plan_id=plan_id,
                        description=description,
                        created_at=now,
                    ),
                )
            )
            if not inserted:
                # Lost a race to a concurrent same-key request (advisory lock normally
                # serializes, but UNIQUE is the backstop) — treat as already completed.
                bal = await PostgresReferralBalanceRepository.get_balance_in_connection(conn, internal_user_id)
                return JSONResponse(
                    {
                        "ok": True,
                        "status": "already_completed",
                        "device_count": new_device_count,
                        "amount_rubles": cost_rubles,
                        "amount_kopecks": cost_kopecks,
                        "balance_rubles_remaining": round((bal.balance_kopecks if bal else 0) / 100, 2),
                    }
                )

            debit = await PostgresReferralBalanceRepository.debit_in_connection(conn, internal_user_id, cost_kopecks)
            if debit is None:
                raise _InsufficientBalance()
            # device_count only — state_label / active_until / plan_id unchanged (no renewal).
            await PostgresSubscriptionSnapshotReader.upsert_state_in_connection(
                conn,
                SubscriptionSnapshot(
                    internal_user_id=internal_user_id,
                    state_label=snap.state_label,
                    active_until_utc=snap.active_until_utc,
                    plan_id=snap.plan_id,
                    device_count=new_device_count,
                ),
            )
            balance_after = debit.balance_kopecks
    except _NoActive:
        return safe_json_error(409, "no_active_subscription")
    except _NotHigher:
        return safe_json_error(400, "device_count_not_higher")
    except _InsufficientBalance:
        return safe_json_error(402, "insufficient_balance")
    except Exception:
        _LOGGER.exception("add-device balance tx failed user=%s", internal_user_id)
        return safe_json_error(500, "balance_payment_failed")

    return JSONResponse(
        {
            "ok": True,
            "status": "completed",
            "device_count": new_device_count,
            "amount_rubles": cost_rubles,
            "amount_kopecks": cost_kopecks,
            "balance_rubles_remaining": round(balance_after / 100, 2),
        }
    )


async def _credit_web_balance_commissions(
    pool,
    payer_internal_user_id: str,
    plan_id: str,
    payment_amount_kopecks: int,
    correlation_id: str,
) -> list[CreditedCommission]:
    """Credit L1/L2 referral commissions for a web balance payment (idempotent by
    description — the same ``correlation_id`` ties commission rows to the debit row
    so a replayed idempotency key re-credits nothing). Returns the commissions that
    were actually credited, so the caller can best-effort notify each referrer."""
    credited: list[CreditedCommission] = []
    async with pool.acquire() as conn, conn.transaction():
        referrers = await PostgresReferralRelationshipRepository.find_referrers_in_connection(
            conn, payer_internal_user_id
        )
        if not referrers:
            return credited
        direct, indirect = resolve_direct_and_indirect_referrers(referrers)
        commissions = build_commissions_for_payment(
            payer_user_id=payer_internal_user_id,
            direct_referrer_user_id=direct,
            indirect_referrer_user_id=indirect,
            plan_id=plan_id,
            payment_amount_kopecks=payment_amount_kopecks,
        )
        now = datetime.now(UTC)
        for comm in commissions:
            dedup_desc = (
                f"web-bal:l{comm.level}:{comm.payer_user_id}:{comm.plan_id}:{payment_amount_kopecks}:{correlation_id}"
            )
            inserted = (
                await PostgresReferralTransactionRepository.append_transaction_if_description_absent_in_connection(
                    conn,
                    ReferralTransactionRecord(
                        transaction_id=f"ref-{uuid.uuid4()}",
                        internal_user_id=comm.referrer_user_id,
                        amount_kopecks=comm.amount_kopecks,
                        transaction_type="referral_credit",
                        related_user_id=comm.payer_user_id,
                        related_plan_id=comm.plan_id,
                        description=dedup_desc,
                        created_at=now,
                    ),
                )
            )
            if inserted:
                await PostgresReferralBalanceRepository.credit_in_connection(
                    conn, comm.referrer_user_id, comm.amount_kopecks
                )
                credited.append(
                    CreditedCommission(
                        referrer_internal_user_id=comm.referrer_user_id,
                        amount_kopecks=comm.amount_kopecks,
                        level=comm.level,
                    )
                )
    return credited
