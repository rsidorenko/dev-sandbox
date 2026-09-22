"""Оплата балансом: атомарные списание, подписка, комиссия и сохраняемая выдача."""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import asyncpg

from app.application.access_grant import access_grant
from app.application.balance_access_jobs import enqueue_access_in_connection, fulfill_access_job
from app.application.balance_commissions import credit_commissions_in_connection
from app.application.bootstrap import Slice1Composition
from app.application.interfaces import SubscriptionSnapshot
from app.application.plan_pricing import get_plan_pricing, resolve_effective_charge
from app.domain.devices import DEFAULT_DEVICE_LIMIT, extra_device_cost
from app.domain.plans import calculate_total_price_kopecks
from app.domain.pricing import WINNER_PROMO
from app.domain.referral import (
    build_commissions_for_payment,
    resolve_direct_and_indirect_referrers,
    resolve_reward_policy,
)
from app.domain.subscription_timing import compute_renewal_active_until
from app.persistence.postgres_referral import (
    PostgresReferralBalanceRepository,
    PostgresReferralTransactionRepository,
)
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.referral_contracts import ReferralTransactionRecord
from app.runtime.fulfillment_processor import (
    CreditedCommission,
    FulfillmentActivationTelegramNotifier,
    _notify_invitee_of_bonus_best_effort,
    _notify_referrer_of_bonus_best_effort,
    _notify_referrers_of_commission_best_effort,
    award_referral_bonus_month_best_effort,
    award_referral_invitee_bonus_best_effort,
)

_LOGGER = logging.getLogger(__name__)


class BalancePaymentStatus(StrEnum):
    COMPLETED = "completed"
    PROVISIONING = "provisioning"
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
    replayed: bool = False


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
    plan = await get_plan_pricing(pool).get_plan(plan_id)
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

    # Применённый промокод: скидка применяется ОДИН РАЗ к charge_kopecks и течёт в debit,
    # result.amount и базу комиссии (паритет карта/баланс — комиссия с discounted суммы).
    # Итог — best-of(промо, глобальная скидка); погашение кода — только если промо выиграл.
    active_promo, charge_kopecks, promo_won = await _resolve_balance_discount(
        pool=pool,
        composition=composition,
        internal_user_id=internal_user_id,
        base_kopecks=total_kopecks,
        plan_id=plan_id,
    )

    if pool is not None:
        result = await _pay_plan_pg(
            pool=pool,
            internal_user_id=internal_user_id,
            plan=plan,
            plan_id=plan_id,
            device_count=device_count,
            total_kopecks=charge_kopecks,
            correlation_id=cid,
            description_prefix=description_prefix,
            vless_provider=vless_provider,
            notifier=notifier,
            active_until_normalization=active_until_normalization,
        )
    else:
        result = await _pay_plan_inmemory(
            composition=composition,
            internal_user_id=internal_user_id,
            plan=plan,
            plan_id=plan_id,
            device_count=device_count,
            total_kopecks=charge_kopecks,
            correlation_id=cid,
            description_prefix=description_prefix,
            vless_provider=vless_provider,
            notifier=notifier,
            active_until_normalization=active_until_normalization,
        )

    # Погашение — при новой принятой оплате, включая ожидание выдачи, и только
    # если промокод выиграл best-of (иначе глобальная скидка зря расходовала бы код). Best-effort.
    if (
        result.status in (BalancePaymentStatus.COMPLETED, BalancePaymentStatus.PROVISIONING)
        and not result.replayed
        and active_promo is not None
        and promo_won
    ):
        await _redeem_balance_promo(
            pool=pool,
            composition=composition,
            active_promo=active_promo,
            internal_user_id=internal_user_id,
            payment_ref=cid,
        )
    return result


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
    now = datetime.now(UTC)
    idempotency_key = f"{description_prefix}: {plan_id} x {device_count} devices [{correlation_id}]"
    credited = ()
    replayed = False
    try:
        async with pool.acquire() as conn, conn.transaction():
            locked = await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext($1))", internal_user_id)
            if not locked:
                return BalancePaymentResult(
                    BalancePaymentStatus.IN_PROGRESS, internal_user_id, correlation_id, plan_id, device_count
                )
            inserted = (
                await PostgresReferralTransactionRepository.append_transaction_if_idempotency_absent_in_connection(
                    conn,
                    ReferralTransactionRecord(
                        transaction_id=f"ref-{uuid.uuid4()}",
                        internal_user_id=internal_user_id,
                        amount_kopecks=-total_kopecks,
                        transaction_type="subscription_payment",
                        related_user_id=None,
                        related_plan_id=plan_id,
                        description=f"Оплата подписки · {plan_id}",
                        idempotency_key=idempotency_key,
                        created_at=now,
                    ),
                )
            )
            existing = await PostgresSubscriptionSnapshotReader.get_for_user_in_connection(conn, internal_user_id)
            if not inserted:
                replayed = True
                balance = await PostgresReferralBalanceRepository.get_balance_in_connection(conn, internal_user_id)
                balance_after = balance.balance_kopecks if balance else 0
                active_until = existing.active_until_utc if existing else None
            else:
                debit = await PostgresReferralBalanceRepository.debit_in_connection(
                    conn, internal_user_id, total_kopecks
                )
                if debit is None:
                    raise _InsufficientBalance()
                base_date = max(now, existing.active_until_utc) if existing and existing.active_until_utc else now
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
                # Оплаченная подписка снова разрешает выдачу; сама выдача подтверждается заданием.
                await conn.execute(
                    "UPDATE subscription_snapshots SET keys_deleted_at=NULL, keys_deactivated_at=NULL"
                    " WHERE internal_user_id=$1",
                    internal_user_id,
                )
                credited = await credit_commissions_in_connection(
                    conn, internal_user_id, plan_id, total_kopecks, correlation_id, prefix="bal"
                )
                await enqueue_access_in_connection(conn, internal_user_id, idempotency_key)
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

    if not replayed:
        if credited and notifier is not None:
            with contextlib.suppress(Exception):
                await _notify_referrers_of_commission_best_effort(
                    pool=pool, notifier=notifier, credited=list(credited), correlation_id=correlation_id
                )
        legacy_bonus = await award_referral_bonus_month_best_effort(
            pool=pool,
            vless_provider=vless_provider,
            payer_internal_user_id=internal_user_id,
            plan_id=plan_id,
            correlation_id=correlation_id,
        )
        invitee_bonus = await award_referral_invitee_bonus_best_effort(
            pool=pool,
            vless_provider=vless_provider,
            payer_internal_user_id=internal_user_id,
            plan_id=plan_id,
            correlation_id=correlation_id,
        )
        if notifier is not None:
            if legacy_bonus is not None:
                await _notify_referrer_of_bonus_best_effort(
                    notifier=notifier, bonus=legacy_bonus, correlation_id=correlation_id
                )
            if invitee_bonus is not None:
                await _notify_invitee_of_bonus_best_effort(
                    notifier=notifier, bonus=invitee_bonus, correlation_id=correlation_id
                )
        if invitee_bonus is not None:
            active_until = invitee_bonus.new_active_until

    job_status = await fulfill_access_job(pool, vless_provider, internal_user_id, idempotency_key)
    status = BalancePaymentStatus.ALREADY_COMPLETED if replayed else BalancePaymentStatus.COMPLETED
    if job_status == "pending":
        status = BalancePaymentStatus.PROVISIONING
    elif job_status == "superseded":
        status = BalancePaymentStatus.NO_ACTIVE
    return BalancePaymentResult(
        status,
        internal_user_id,
        correlation_id,
        plan_id=plan_id,
        device_count=device_count,
        active_until_utc=active_until,
        amount_kopecks=0 if replayed else total_kopecks,
        balance_after_kopecks=balance_after,
        credited_commissions=credited,
        replayed=replayed,
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
            description=f"Оплата подписки · {plan_id}",
            idempotency_key=f"{description_prefix}: {plan_id} x {device_count} devices [{correlation_id}]",
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
    vless_provider: Any | None = None,
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
        result = await _add_device_pg(
            pool=pool,
            internal_user_id=internal_user_id,
            new_device_count=new_device_count,
            correlation_id=cid,
            description_prefix=description_prefix,
        )
        if result.status in (BalancePaymentStatus.COMPLETED, BalancePaymentStatus.ALREADY_COMPLETED):
            job_status = await fulfill_access_job(
                pool, vless_provider, internal_user_id, f"{description_prefix} → {new_device_count} [{cid}]"
            )
            if job_status == "pending":
                return replace(result, status=BalancePaymentStatus.PROVISIONING)
            if job_status == "superseded":
                return replace(result, status=BalancePaymentStatus.NO_ACTIVE)
        return result
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
    idempotency_key = f"{description_prefix} → {new_device_count} [{correlation_id}]"
    description = f"Добавление устройств · до {new_device_count}"
    now = datetime.now(UTC)
    cost_kopecks = 0
    balance_after: int = 0
    new_count_resolved = new_device_count
    active_promo_add: Any = None

    try:
        async with pool.acquire() as conn, conn.transaction():
            locked = await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext($1))", internal_user_id)
            if not locked:
                return BalancePaymentResult(BalancePaymentStatus.IN_PROGRESS, internal_user_id, correlation_id)

            # Dedup FIRST (before the snapshot pre-check): a replayed key must
            # short-circuit here, otherwise a prior success (device_count already
            # raised) would re-fail `new <= current`.
            already = await conn.fetchval(
                "SELECT 1 FROM referral_transactions WHERE internal_user_id=$1 AND idempotency_key=$2",
                internal_user_id,
                idempotency_key,
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
            if snap is None or await access_grant(pool=conn, internal_user_id=internal_user_id) is None:
                raise _NoActive()
            current = snap.device_count or DEFAULT_DEVICE_LIMIT
            if new_device_count <= current:
                raise _NotHigher()
            plan_id = snap.plan_id or "1m"
            # Только duration_days (оверрайдами не меняется) — код-дефолт.
            from app.domain.plans import get_plan as _get_plan

            plan = _get_plan(plan_id)
            if plan is None:
                raise _NoActive()
            cost_rubles = extra_device_cost(new_device_count, current, plan.duration_days)
            if cost_rubles <= 0:
                raise _NotHigher()
            cost_kopecks = cost_rubles * 100

            # Промокод: discounted debit. resolve_active_promo читает promo_* на отдельном
            # соединении из pool — безопасно внутри транзакции (другие таблицы, только чтение;
            # advisory lock сериализует per-user операции, рассинхрона current здесь быть не может).
            from app.application.promo_service import resolve_active_promo

            applied_repo, code_repo, redemption_repo, _snap = _balance_promo_repos(pool, None)
            active_promo_add = await resolve_active_promo(
                pool=pool,
                promo_applied_repo=applied_repo,
                promo_code_repo=code_repo,
                promo_redemption_repo=redemption_repo,
                snapshots=_snap,
                internal_user_id=internal_user_id,
                base_rubles=cost_rubles,
                is_addon=True,
            )
            if active_promo_add is not None:
                cost_kopecks = active_promo_add.price.discounted_kopecks

            inserted = (
                await PostgresReferralTransactionRepository.append_transaction_if_idempotency_absent_in_connection(
                    conn,
                    ReferralTransactionRecord(
                        transaction_id=f"ref-{uuid.uuid4()}",
                        internal_user_id=internal_user_id,
                        amount_kopecks=-cost_kopecks,
                        transaction_type="subscription_payment",
                        related_user_id=None,
                        related_plan_id=plan_id,
                        description=description,
                        idempotency_key=idempotency_key,
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
            await enqueue_access_in_connection(conn, internal_user_id, idempotency_key)
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

    # Погашение промокода после успешной транзакции (best-effort, идемпотентно).
    if active_promo_add is not None:
        await _redeem_balance_promo(
            pool=pool,
            composition=None,
            active_promo=active_promo_add,
            internal_user_id=internal_user_id,
            payment_ref=correlation_id,
        )

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

    # Промокод (in-memory путь): discounted debit + погашение на успех (composition-репозитории).
    from app.application.promo_service import redeem_promo_on_success, resolve_active_promo

    active_promo_add = await resolve_active_promo(
        pool=None,
        promo_applied_repo=composition.promo_applied_repo,
        promo_code_repo=composition.promo_code_repo,
        promo_redemption_repo=composition.promo_redemption_repo,
        snapshots=composition.snapshots,
        internal_user_id=internal_user_id,
        base_rubles=cost_rubles,
        is_addon=True,
    )
    if active_promo_add is not None:
        cost_kopecks = active_promo_add.price.discounted_kopecks

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
            description=f"Добавление устройств · до {new_device_count}",
            idempotency_key=f"{description_prefix}: {current} → {new_device_count} [{correlation_id}]",
            created_at=now,
        )
    )
    if active_promo_add is not None:
        await redeem_promo_on_success(
            pool=None,
            promo_applied_repo=composition.promo_applied_repo,
            promo_code_repo=composition.promo_code_repo,
            promo_redemption_repo=composition.promo_redemption_repo,
            internal_user_id=internal_user_id,
            code=active_promo_add.code,
            base_rubles=active_promo_add.price.base_rubles,
            discounted_rubles=active_promo_add.price.discounted_rubles,
            discount_kopecks=active_promo_add.price.discount_kopecks,
            payment_ref=correlation_id,
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


def _balance_promo_repos(pool: asyncpg.Pool | None, composition: Slice1Composition | None) -> tuple[Any, Any, Any, Any]:
    """Репозитории промокодов для баланс-пути: (applied, code, redemption, snapshots).

    На postgres инстанциируем из pool (web-обработчик передаёт composition=None), на
    in-memory берём из composition (бот/unit-тесты). snapshots нужен только in-memory
    ветке compute_is_new_user; на postgres is_new_user считается через SQL на pool.
    """
    if pool is not None:
        from app.persistence.postgres_promo import (
            PostgresPromoAppliedRepository,
            PostgresPromoCodeRepository,
            PostgresPromoRedemptionRepository,
        )

        return (
            PostgresPromoAppliedRepository(pool),
            PostgresPromoCodeRepository(pool),
            PostgresPromoRedemptionRepository(pool),
            None,
        )
    assert composition is not None  # invariant: pool is None ⇒ composition задан (бот/тесты)
    return (
        composition.promo_applied_repo,
        composition.promo_code_repo,
        composition.promo_redemption_repo,
        composition.snapshots,
    )


async def _resolve_balance_discount(
    *,
    pool: asyncpg.Pool | None,
    composition: Slice1Composition | None,
    internal_user_id: str,
    base_kopecks: int,
    plan_id: str,
) -> tuple[Any, int, bool]:
    """Возвращает (ActivePromo | None, charge_kopecks, promo_won).

    charge — best-of(промокод, глобальная скидка) на одной базе; promo_won —
    выиграл ли именно промокод (тогда и только тогда код гасится на успехе)."""
    from app.application.promo_service import resolve_active_promo

    base_rubles = base_kopecks // 100
    if base_rubles <= 0:
        return None, base_kopecks, False
    applied, code_repo, redemption_repo, snapshots = _balance_promo_repos(pool, composition)
    active = await resolve_active_promo(
        pool=pool,
        promo_applied_repo=applied,
        promo_code_repo=code_repo,
        promo_redemption_repo=redemption_repo,
        snapshots=snapshots,
        internal_user_id=internal_user_id,
        base_rubles=base_rubles,
        plan_id=plan_id,
    )
    resolved = resolve_effective_charge(
        base_rubles=base_rubles,
        plan_id=plan_id,
        promo=active.promo if active is not None else None,
        global_discount=await get_plan_pricing(pool).get_global_discount(),
    )
    return active, resolved.charge_kopecks, resolved.price.winner == WINNER_PROMO


async def _redeem_balance_promo(
    *,
    pool: asyncpg.Pool | None,
    composition: Slice1Composition | None,
    active_promo: Any,
    internal_user_id: str,
    payment_ref: str,
) -> None:
    """Best-effort погашение промокода при успехе баланс-оплаты (атомарно, идемпотентно)."""
    from app.application.promo_service import redeem_promo_on_success

    applied, code_repo, redemption, _snapshots = _balance_promo_repos(pool, composition)
    await redeem_promo_on_success(
        pool=pool,
        promo_applied_repo=applied,
        promo_code_repo=code_repo,
        promo_redemption_repo=redemption,
        internal_user_id=internal_user_id,
        code=active_promo.code,
        base_rubles=active_promo.price.base_rubles,
        discounted_rubles=active_promo.price.discounted_rubles,
        discount_kopecks=active_promo.price.discount_kopecks,
        payment_ref=payment_ref,
    )


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
    reward_policy = resolve_reward_policy(referrers)
    commissions = build_commissions_for_payment(
        payer_user_id=payer_internal_user_id,
        direct_referrer_user_id=direct,
        indirect_referrer_user_id=indirect,
        plan_id=plan_id,
        payment_amount_kopecks=payment_amount_kopecks,
        reward_policy=reward_policy,
    )
    now = datetime.now(UTC)
    for comm in commissions:
        idempotency_key = (
            f"bal:l{comm.level}:{comm.payer_user_id}:{comm.plan_id}:{payment_amount_kopecks}:{correlation_id}"
        )
        inserted = await composition.referral_transaction_repo.append_transaction_if_idempotency_absent(
            ReferralTransactionRecord(
                transaction_id=f"ref-{uuid.uuid4()}",
                internal_user_id=comm.referrer_user_id,
                amount_kopecks=comm.amount_kopecks,
                transaction_type="referral_credit",
                related_user_id=comm.payer_user_id,
                related_plan_id=comm.plan_id,
                description=f"Реферальное начисление · уровень {comm.level}",
                idempotency_key=idempotency_key,
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
