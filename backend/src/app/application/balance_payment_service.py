"""Balance-payment service — single source of truth for paying from the referral
balance, shared by the web API and (Phase 3 PR-2) the Telegram bot.

Form = web-shape: 3-layer idempotency (``X-Idempotency-Key``/correlation_id baked
into the audit-row ``description`` → UNIQUE dedup; ``pg_try_advisory_xact_lock`` for
multi-worker concurrency), audit-row written FIRST inside the debit transaction
(no orphan on crash — closes M6), atomic 4-step VLESS-failure compensation, and
referral-commission credit + best-effort referrer notify (closes M5 for both
transports once the bot adopts the service).

Callers are thin adapters mapping :class:`BalancePaymentResult` to their transport
(JSON response for web, ``(text, keyboard)`` for the bot).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import asyncpg

from app.application.bootstrap import Slice1Composition
from app.application.interfaces import SubscriptionSnapshot
from app.domain.devices import DEFAULT_DEVICE_LIMIT, extra_device_cost
from app.domain.plans import calculate_total_price_kopecks, get_plan
from app.domain.referral import build_commissions_for_payment, resolve_direct_and_indirect_referrers
from app.domain.subscription_timing import compute_renewal_active_until
from app.persistence.postgres_referral import (
    PostgresReferralBalanceRepository,
    PostgresReferralRelationshipRepository,
    PostgresReferralTransactionRepository,
)
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.referral_contracts import ReferralTransactionRecord
from app.runtime.fulfillment_processor import (
    CreditedCommission,
    FulfillmentActivationTelegramNotifier,
    _notify_referrers_of_commission_best_effort,
)

_LOGGER = logging.getLogger(__name__)


class BalancePaymentStatus(StrEnum):
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    INSUFFICIENT = "insufficient_balance"
    NO_ACTIVE = "no_active_subscription"
    NOT_HIGHER = "device_count_not_higher"
    IN_PROGRESS = "payment_in_progress"
    INVALID_PLAN = "invalid_plan_id"
    INVALID_DEVICE_COUNT = "invalid_device_count"
    VLESS_FAILED = "vless_creation_failed"
    INTERNAL_ERROR = "balance_payment_failed"


@dataclass(frozen=True, slots=True)
class BalancePaymentResult:
    status: BalancePaymentStatus
    internal_user_id: str
    correlation_id: str
    plan_id: str | None = None
    device_count: int = 0
    active_until_utc: datetime | None = None
    amount_kopecks: int = 0
    balance_after_kopecks: int = 0
    credited_commissions: tuple[CreditedCommission, ...] = field(default_factory=tuple)


# Control-flow exceptions raised inside a transaction so the atomic audit-insert
# rolls back with the debit.
class _InsufficientBalance(Exception):
    pass


class _NoActive(Exception):
    pass


class _NotHigher(Exception):
    pass


# ── Plan purchase (subscribe / renew) ────────────────────────────────────────


async def pay_plan_from_balance(
    *,
    pool: asyncpg.Pool | None,
    composition: Slice1Composition | None,
    internal_user_id: str,
    plan_id: str,
    device_count: int,
    correlation_id: str | None = None,
    description_prefix: str = "balance payment",
    device_count_min: int = DEFAULT_DEVICE_LIMIT,
    device_count_max: int = 20,
    vless_provider: Any | None = None,
    notifier: FulfillmentActivationTelegramNotifier | None = None,
    active_until_normalization: str = "wall_clock",
) -> BalancePaymentResult:
    plan = get_plan(plan_id)
    if plan is None:
        return BalancePaymentResult(
            BalancePaymentStatus.INVALID_PLAN, internal_user_id, correlation_id or "", plan_id=plan_id
        )
    if not (device_count_min <= device_count <= device_count_max):
        return BalancePaymentResult(
            BalancePaymentStatus.INVALID_DEVICE_COUNT,
            internal_user_id,
            correlation_id or "",
            plan_id=plan_id,
            device_count=device_count,
        )
    total_kopecks = calculate_total_price_kopecks(plan, device_count)
    cid = correlation_id or f"bal-pay-{uuid.uuid4()}"

    if pool is not None:
        return await _pay_plan_pg(
            pool=pool,
            internal_user_id=internal_user_id,
            plan=plan,
            plan_id=plan_id,
            device_count=device_count,
            total_kopecks=total_kopecks,
            correlation_id=cid,
            description_prefix=description_prefix,
            vless_provider=vless_provider,
            notifier=notifier,
            active_until_normalization=active_until_normalization,
        )
    return await _pay_plan_inmemory(
        composition=composition,
        internal_user_id=internal_user_id,
        plan=plan,
        plan_id=plan_id,
        device_count=device_count,
        total_kopecks=total_kopecks,
        correlation_id=cid,
        description_prefix=description_prefix,
        vless_provider=vless_provider,
        notifier=notifier,
        active_until_normalization=active_until_normalization,
    )


async def _pay_plan_pg(
    *,
    pool: asyncpg.Pool,
    internal_user_id: str,
    plan,
    plan_id: str,
    device_count: int,
    total_kopecks: int,
    correlation_id: str,
    description_prefix: str,
    vless_provider: Any | None,
    notifier: FulfillmentActivationTelegramNotifier | None,
    active_until_normalization: str,
) -> BalancePaymentResult:
    pre_payment_snap: SubscriptionSnapshot | None = None
    with contextlib.suppress(Exception):
        pre_payment_snap = await PostgresSubscriptionSnapshotReader(pool).get_for_user(internal_user_id)

    now = datetime.now(UTC)
    description = f"{description_prefix}: {plan_id} x {device_count} devices [{correlation_id}]"
    active_until: datetime | None = None
    balance_after: int = 0

    try:
        async with pool.acquire() as conn, conn.transaction():
            locked = await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext($1))", internal_user_id)
            if not locked:
                return BalancePaymentResult(
                    BalancePaymentStatus.IN_PROGRESS, internal_user_id, correlation_id, plan_id, device_count
                )

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
                return BalancePaymentResult(
                    BalancePaymentStatus.ALREADY_COMPLETED,
                    internal_user_id,
                    correlation_id,
                    plan_id=plan_id,
                    device_count=device_count,
                    active_until_utc=existing.active_until_utc if existing else None,
                    amount_kopecks=0,
                    balance_after_kopecks=bal.balance_kopecks if bal else 0,
                )

            debit = await PostgresReferralBalanceRepository.debit_in_connection(conn, internal_user_id, total_kopecks)
            if debit is None:
                raise _InsufficientBalance()

            existing_snap = await PostgresSubscriptionSnapshotReader.get_for_user_in_connection(conn, internal_user_id)
            base_date = now
            if existing_snap and existing_snap.active_until_utc and existing_snap.active_until_utc > now:
                base_date = existing_snap.active_until_utc
            active_until = compute_renewal_active_until(base_date, plan.duration_days, active_until_normalization)
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
        return BalancePaymentResult(
            BalancePaymentStatus.INSUFFICIENT,
            internal_user_id,
            correlation_id,
            plan_id=plan_id,
            device_count=device_count,
        )
    except Exception:
        _LOGGER.exception("balance payment tx failed user=%s", internal_user_id)
        return BalancePaymentResult(
            BalancePaymentStatus.INTERNAL_ERROR, internal_user_id, correlation_id, plan_id, device_count
        )

    # VLESS (outside tx — network IO). Branch on keys lifecycle; atomic compensation on failure.
    if vless_provider is not None:
        vless_failed = await _ensure_vless_keys_atomic(
            pool=pool,
            vless_provider=vless_provider,
            internal_user_id=internal_user_id,
            device_count=device_count,
            pre_payment_snap=pre_payment_snap,
            total_kopecks=total_kopecks,
            plan_id=plan_id,
            description=description,
            correlation_id=correlation_id,
        )
        if vless_failed:
            return BalancePaymentResult(
                BalancePaymentStatus.VLESS_FAILED, internal_user_id, correlation_id, plan_id, device_count
            )

    credited = await _credit_commissions_best_effort(
        pool, internal_user_id, plan_id, total_kopecks, correlation_id, prefix="bal"
    )
    if credited and notifier is not None:
        with contextlib.suppress(Exception):
            await _notify_referrers_of_commission_best_effort(
                pool=pool, notifier=notifier, credited=list(credited), correlation_id=correlation_id
            )

    return BalancePaymentResult(
        BalancePaymentStatus.COMPLETED,
        internal_user_id,
        correlation_id,
        plan_id=plan_id,
        device_count=device_count,
        active_until_utc=active_until,
        amount_kopecks=total_kopecks,
        balance_after_kopecks=balance_after,
        credited_commissions=credited,
    )


async def _pay_plan_inmemory(
    *,
    composition: Slice1Composition,
    internal_user_id: str,
    plan,
    plan_id: str,
    device_count: int,
    total_kopecks: int,
    correlation_id: str,
    description_prefix: str,
    vless_provider: Any | None,
    notifier: FulfillmentActivationTelegramNotifier | None,
    active_until_normalization: str,
) -> BalancePaymentResult:
    """In-memory seam for the bot unit-tests (composition repos, no tx/lock)."""
    balance_record = await composition.referral_balance_repo.get_balance(internal_user_id)
    balance_kopecks = balance_record.balance_kopecks if balance_record else 0
    if balance_kopecks < total_kopecks:
        return BalancePaymentResult(
            BalancePaymentStatus.INSUFFICIENT,
            internal_user_id,
            correlation_id,
            plan_id=plan_id,
            device_count=device_count,
        )
    debit_result = await composition.referral_balance_repo.debit(internal_user_id, total_kopecks)
    if debit_result is None:
        return BalancePaymentResult(
            BalancePaymentStatus.INSUFFICIENT,
            internal_user_id,
            correlation_id,
            plan_id=plan_id,
            device_count=device_count,
        )
    now = datetime.now(UTC)
    existing_snap = await composition.snapshots.get_for_user(internal_user_id)
    base_date = now
    if existing_snap and existing_snap.active_until_utc and existing_snap.active_until_utc > now:
        base_date = existing_snap.active_until_utc
    active_until = compute_renewal_active_until(base_date, plan.duration_days, active_until_normalization)
    try:
        await composition.snapshots.upsert_state(
            SubscriptionSnapshot(
                internal_user_id=internal_user_id,
                state_label="active",
                active_until_utc=active_until,
                plan_id=plan_id,
                device_count=device_count,
            )
        )
    except Exception:
        with contextlib.suppress(Exception):
            await composition.referral_balance_repo.credit(internal_user_id, total_kopecks)
        return BalancePaymentResult(
            BalancePaymentStatus.INTERNAL_ERROR, internal_user_id, correlation_id, plan_id, device_count
        )
    if vless_provider is not None:
        with contextlib.suppress(Exception):
            await vless_provider.create_user(internal_user_id=internal_user_id, device_count=device_count)
    await composition.referral_transaction_repo.append_transaction(
        ReferralTransactionRecord(
            transaction_id=correlation_id,
            internal_user_id=internal_user_id,
            amount_kopecks=-total_kopecks,
            transaction_type="subscription_payment",
            related_user_id=None,
            related_plan_id=plan_id,
            description=f"{description_prefix}: {plan_id} x {device_count} devices [{correlation_id}]",
            created_at=now,
        )
    )
    credited = await _credit_commissions_inmemory(composition, internal_user_id, plan_id, total_kopecks, correlation_id)
    return BalancePaymentResult(
        BalancePaymentStatus.COMPLETED,
        internal_user_id,
        correlation_id,
        plan_id=plan_id,
        device_count=device_count,
        active_until_utc=active_until,
        amount_kopecks=total_kopecks,
        balance_after_kopecks=debit_result.balance_kopecks,
        credited_commissions=credited,
    )


# ── Add device (top-up device_count, no renewal) ─────────────────────────────


async def add_device_from_balance(
    *,
    pool: asyncpg.Pool | None,
    composition: Slice1Composition | None,
    internal_user_id: str,
    new_device_count: int,
    correlation_id: str | None = None,
    description_prefix: str = "add device",
    device_count_min: int = DEFAULT_DEVICE_LIMIT,
    device_count_max: int = 20,
) -> BalancePaymentResult:
    if not (device_count_min <= new_device_count <= device_count_max):
        return BalancePaymentResult(
            BalancePaymentStatus.INVALID_DEVICE_COUNT,
            internal_user_id,
            correlation_id or "",
            device_count=new_device_count,
        )
    cid = correlation_id or f"add-dev-{uuid.uuid4()}"
    if pool is not None:
        return await _add_device_pg(
            pool=pool,
            internal_user_id=internal_user_id,
            new_device_count=new_device_count,
            correlation_id=cid,
            description_prefix=description_prefix,
        )
    return await _add_device_inmemory(
        composition=composition,
        internal_user_id=internal_user_id,
        new_device_count=new_device_count,
        correlation_id=cid,
        description_prefix=description_prefix,
    )


async def _add_device_pg(
    *,
    pool: asyncpg.Pool,
    internal_user_id: str,
    new_device_count: int,
    correlation_id: str,
    description_prefix: str,
) -> BalancePaymentResult:
    # Description is deterministic in the REQUEST alone so a replayed key matches
    # even after device_count has already been raised.
    description = f"{description_prefix} → {new_device_count} [{correlation_id}]"
    now = datetime.now(UTC)
    cost_kopecks = 0
    balance_after: int = 0
    new_count_resolved = new_device_count

    try:
        async with pool.acquire() as conn, conn.transaction():
            locked = await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext($1))", internal_user_id)
            if not locked:
                return BalancePaymentResult(BalancePaymentStatus.IN_PROGRESS, internal_user_id, correlation_id)

            # Dedup FIRST (before the snapshot pre-check): a replayed key must
            # short-circuit here, otherwise a prior success (device_count already
            # raised) would re-fail `new <= current`.
            already = await conn.fetchval(
                "SELECT 1 FROM referral_transactions WHERE internal_user_id=$1 AND description=$2",
                internal_user_id,
                description,
            )
            if already:
                bal = await PostgresReferralBalanceRepository.get_balance_in_connection(conn, internal_user_id)
                snap_now = await PostgresSubscriptionSnapshotReader.get_for_user_in_connection(conn, internal_user_id)
                return BalancePaymentResult(
                    BalancePaymentStatus.ALREADY_COMPLETED,
                    internal_user_id,
                    correlation_id,
                    device_count=(snap_now.device_count if snap_now else new_device_count),
                    amount_kopecks=0,
                    balance_after_kopecks=bal.balance_kopecks if bal else 0,
                )

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
                bal = await PostgresReferralBalanceRepository.get_balance_in_connection(conn, internal_user_id)
                return BalancePaymentResult(
                    BalancePaymentStatus.ALREADY_COMPLETED,
                    internal_user_id,
                    correlation_id,
                    plan_id=plan_id,
                    device_count=new_device_count,
                    amount_kopecks=0,
                    balance_after_kopecks=bal.balance_kopecks if bal else 0,
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
        return BalancePaymentResult(BalancePaymentStatus.NO_ACTIVE, internal_user_id, correlation_id)
    except _NotHigher:
        return BalancePaymentResult(BalancePaymentStatus.NOT_HIGHER, internal_user_id, correlation_id)
    except _InsufficientBalance:
        return BalancePaymentResult(BalancePaymentStatus.INSUFFICIENT, internal_user_id, correlation_id)
    except Exception:
        _LOGGER.exception("add-device balance tx failed user=%s", internal_user_id)
        return BalancePaymentResult(BalancePaymentStatus.INTERNAL_ERROR, internal_user_id, correlation_id)

    return BalancePaymentResult(
        BalancePaymentStatus.COMPLETED,
        internal_user_id,
        correlation_id,
        device_count=new_count_resolved,
        amount_kopecks=cost_kopecks,
        balance_after_kopecks=balance_after,
    )


async def _add_device_inmemory(
    *,
    composition: Slice1Composition,
    internal_user_id: str,
    new_device_count: int,
    correlation_id: str,
    description_prefix: str,
) -> BalancePaymentResult:
    snap = await composition.snapshots.get_for_user(internal_user_id)
    if snap is None or snap.state_label != "active":
        return BalancePaymentResult(BalancePaymentStatus.NO_ACTIVE, internal_user_id, correlation_id)
    current = snap.device_count or DEFAULT_DEVICE_LIMIT
    if new_device_count <= current:
        return BalancePaymentResult(BalancePaymentStatus.NOT_HIGHER, internal_user_id, correlation_id)
    from app.domain.plans import get_plan as _get_plan

    plan_id = snap.plan_id or "1m"
    plan = _get_plan(plan_id)
    if plan is None:
        return BalancePaymentResult(BalancePaymentStatus.NO_ACTIVE, internal_user_id, correlation_id)
    cost_rubles = extra_device_cost(new_device_count, current, plan.duration_days)
    if cost_rubles <= 0:
        return BalancePaymentResult(BalancePaymentStatus.NOT_HIGHER, internal_user_id, correlation_id)
    cost_kopecks = cost_rubles * 100

    debit_result = await composition.referral_balance_repo.debit(internal_user_id, cost_kopecks)
    if debit_result is None:
        return BalancePaymentResult(BalancePaymentStatus.INSUFFICIENT, internal_user_id, correlation_id)
    try:
        await composition.snapshots.upsert_state(
            SubscriptionSnapshot(
                internal_user_id=internal_user_id,
                state_label=snap.state_label,
                active_until_utc=snap.active_until_utc,
                plan_id=snap.plan_id,
                device_count=new_device_count,
            )
        )
    except Exception:
        with contextlib.suppress(Exception):
            await composition.referral_balance_repo.credit(internal_user_id, cost_kopecks)
        return BalancePaymentResult(BalancePaymentStatus.INTERNAL_ERROR, internal_user_id, correlation_id)
    now = datetime.now(UTC)
    await composition.referral_transaction_repo.append_transaction(
        ReferralTransactionRecord(
            transaction_id=correlation_id,
            internal_user_id=internal_user_id,
            amount_kopecks=-cost_kopecks,
            transaction_type="subscription_payment",
            related_user_id=None,
            related_plan_id=plan_id,
            description=f"{description_prefix}: {current} → {new_device_count} [{correlation_id}]",
            created_at=now,
        )
    )
    return BalancePaymentResult(
        BalancePaymentStatus.COMPLETED,
        internal_user_id,
        correlation_id,
        device_count=new_device_count,
        amount_kopecks=cost_kopecks,
        balance_after_kopecks=debit_result.balance_kopecks,
    )


# ── Shared helpers ───────────────────────────────────────────────────────────


async def _ensure_vless_keys_atomic(
    *,
    pool: asyncpg.Pool,
    vless_provider: Any,
    internal_user_id: str,
    device_count: int,
    pre_payment_snap: SubscriptionSnapshot | None,
    total_kopecks: int,
    plan_id: str,
    description: str,
    correlation_id: str,
) -> bool:
    """Branch create/activate by keys lifecycle; on failure run atomic 4-step
    compensation. Returns True if VLESS FAILED (caller maps to VLESS_FAILED)."""
    key_snap = None
    with contextlib.suppress(Exception):
        key_snap = await pool.fetchrow(
            "SELECT keys_deactivated_at, keys_deleted_at FROM subscription_snapshots WHERE internal_user_id=$1",
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
            _LOGGER.warning("balance vless retry user=%s attempt=%d/3", internal_user_id, attempt + 1)
            await asyncio.sleep(2.0)
    if vless_ok:
        with contextlib.suppress(Exception):
            await pool.execute(
                "UPDATE subscription_snapshots SET keys_deactivated_at=NULL, keys_deleted_at=NULL,"
                " updated_at=NOW() WHERE internal_user_id=$1",
                internal_user_id,
            )
        return False

    _LOGGER.error("balance vless FAILED after retries — refunding user=%s", internal_user_id)
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
                f"balance refund (vless failed): {plan_id} [{correlation_id}]",
            )
    except Exception:
        _LOGGER.error(
            "balance compensation tx FAILED user=%s cid=%s — debit left in place,"
            " replay will return already_completed; manual reconcile needed",
            internal_user_id,
            correlation_id,
            exc_info=True,
        )
    return True


async def _credit_commissions_best_effort(
    pool: asyncpg.Pool,
    payer_internal_user_id: str,
    plan_id: str,
    payment_amount_kopecks: int,
    correlation_id: str,
    *,
    prefix: str,
) -> tuple[CreditedCommission, ...]:
    """Credit L1/L2 referral commissions for a balance payment (postgres). Returns the
    commissions that were actually credited, so the caller can best-effort notify."""
    credited: list[CreditedCommission] = []
    try:
        async with pool.acquire() as conn, conn.transaction():
            referrers = await PostgresReferralRelationshipRepository.find_referrers_in_connection(
                conn, payer_internal_user_id
            )
            if not referrers:
                return ()
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
                    f"{prefix}:l{comm.level}:{comm.payer_user_id}:{comm.plan_id}:"
                    f"{payment_amount_kopecks}:{correlation_id}"
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
    except Exception:
        _LOGGER.warning(
            "balance commission credit failed payer=%s cid=%s — referrer may lose commission",
            payer_internal_user_id,
            correlation_id,
            exc_info=True,
        )
        return ()
    return tuple(credited)


async def _credit_commissions_inmemory(
    composition: Slice1Composition,
    payer_internal_user_id: str,
    plan_id: str,
    payment_amount_kopecks: int,
    correlation_id: str,
) -> tuple[CreditedCommission, ...]:
    """In-memory commission credit (composition repos) — for the bot unit-test seam."""
    credited: list[CreditedCommission] = []
    referrers = await composition.referral_relationship_repo.find_referrers(payer_internal_user_id)
    if not referrers:
        return ()
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
        dedup_desc = f"bal:l{comm.level}:{comm.payer_user_id}:{comm.plan_id}:{payment_amount_kopecks}:{correlation_id}"
        inserted = await composition.referral_transaction_repo.append_transaction_if_description_absent(
            ReferralTransactionRecord(
                transaction_id=f"ref-{uuid.uuid4()}",
                internal_user_id=comm.referrer_user_id,
                amount_kopecks=comm.amount_kopecks,
                transaction_type="referral_credit",
                related_user_id=comm.payer_user_id,
                related_plan_id=comm.plan_id,
                description=dedup_desc,
                created_at=now,
            )
        )
        if inserted:
            await composition.referral_balance_repo.credit(comm.referrer_user_id, comm.amount_kopecks)
            credited.append(
                CreditedCommission(
                    referrer_internal_user_id=comm.referrer_user_id,
                    amount_kopecks=comm.amount_kopecks,
                    level=comm.level,
                )
            )
    return tuple(credited)
