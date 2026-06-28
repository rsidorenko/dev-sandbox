"""Shared fulfillment processor — UC-04 ingest, UC-05 apply, snapshot, VLESS, referral, notification.

Reused by both the provider-agnostic fulfillment ingress and the YooKassa webhook.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import asyncpg

from app.application.billing_ingestion import NormalizedBillingFactInput
from app.application.interfaces import SubscriptionSnapshot
from app.bot_transport.message_catalog import render_telegram_outbound_plan
from app.bot_transport.outbound import build_fulfillment_success_notification_plan
from app.domain.plans import DEFAULT_DEVICE_LIMIT, get_plan
from app.persistence.billing_events_ledger_contracts import (
    BillingEventAmountCurrency,
    BillingEventLedgerStatus,
)
from app.persistence.billing_subscription_apply_contracts import BillingSubscriptionApplyOutcome
from app.persistence.postgres_billing_ingestion_atomic import PostgresAtomicBillingIngestion
from app.persistence.postgres_billing_subscription_apply import PostgresAtomicUC05SubscriptionApply
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository, telegram_user_id_from_internal
from app.shared.types import OperationOutcomeCategory

_LOGGER = logging.getLogger(__name__)

_EVENT_TYPE_SUBSCRIPTION_ACTIVATED = "subscription_activated"
_MAX_SUBSCRIPTION_PERIOD_DAYS = 3660


class FulfillmentTelemetry(Protocol):
    async def emit(self, *, decision: str, reason_bucket: str) -> None: ...


class FulfillmentActivationTelegramNotifier(Protocol):
    async def send_subscription_activated_notice(
        self,
        *,
        telegram_user_id: int,
        text: str,
        reply_markup: dict[str, Any] | None,
        correlation_id: str,
    ) -> None: ...


class NoopFulfillmentTelemetry:
    async def emit(self, *, decision: str, reason_bucket: str) -> None:
        _ = (decision, reason_bucket)


@dataclass(frozen=True, slots=True)
class FulfillmentInput:
    provider_key: str
    external_event_id: str
    external_payment_id: str
    telegram_user_id: int
    internal_user_id: str
    paid_at: datetime
    period_days: int
    amount_kopecks: int | None = None
    # Device limit to apply on the snapshot. Defaults to the plan default; the
    # YooKassa webhook passes the device_count the user paid for so a card
    # purchase with extra devices actually grants those devices (previously the
    # snapshot was always reset to DEFAULT_DEVICE_LIMIT, ignoring the purchase).
    device_count: int = DEFAULT_DEVICE_LIMIT


@dataclass(frozen=True, slots=True)
class FulfillmentResult:
    operation_outcome: OperationOutcomeCategory
    idempotent_replay: bool
    apply_outcome: BillingSubscriptionApplyOutcome | None
    correlation_id: str


@dataclass(frozen=True, slots=True)
class CreditedCommission:
    """A referral commission that was actually credited to a referrer's balance on a payment
    (the idempotent transaction insert succeeded). Drives the best-effort referrer notification."""
    referrer_internal_user_id: str
    amount_kopecks: int
    level: int  # 1 (direct) or 2 (indirect)


def _plan_id_from_period_days(period_days: int) -> str:
    _KNOWN: dict[int, str] = {
        1: "1d",
        7: "7d",
        14: "14d",
        30: "1m",
        90: "3m",
        180: "6m",
        365: "365d",
    }
    return _KNOWN.get(period_days, f"custom:{period_days}")


# Every real payment in this system is denominated in RUB (YooKassa creates RUB
# payments; the provider-agnostic ingress and the operator grant tool are RUB too).
# Amount is expressed in kopecks (minor units), matching billing_events_ledger.
_LEDGER_CURRENCY_CODE = "RUB"


def _ledger_amount_currency(amount_kopecks: int | None) -> BillingEventAmountCurrency | None:
    """Build the normalized amount/currency for the billing ledger from paid kopecks.

    Returns None when the paid amount is unknown (the ledger amount column is
    intentionally nullable). Otherwise captures the paid amount so the append-only
    ledger can answer "how much was actually paid" for reconciliation.
    """
    if amount_kopecks is None:
        return None
    return BillingEventAmountCurrency(amount_minor_units=amount_kopecks, currency_code=_LEDGER_CURRENCY_CODE)


async def _send_activation_notice_best_effort(
    notifier: FulfillmentActivationTelegramNotifier,
    *,
    telegram_user_id: int,
    text: str,
    reply_markup: dict[str, Any] | None,
    correlation_id: str,
) -> None:
    try:
        await notifier.send_subscription_activated_notice(
            telegram_user_id=telegram_user_id,
            text=text,
            reply_markup=reply_markup,
            correlation_id=correlation_id,
        )
    except Exception:
        return


async def _ensure_vless_keys_after_payment(
    *,
    pool: asyncpg.Pool,
    vless_provider: Any,
    internal_user_id: str,
    telegram_user_id: int | None = None,
    activation_notifier: FulfillmentActivationTelegramNotifier | None = None,
    correlation_id: str = "",
    period_days: int = 30,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> None:
    import asyncio as _asyncio

    from app.issuance.vless_provider import VlessProviderOutcome

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            snap_check = await pool.fetchrow(
                """SELECT keys_deactivated_at, keys_deleted_at, device_count FROM subscription_snapshots
                   WHERE internal_user_id = $1""",
                internal_user_id,
            )
            dc = (snap_check.get("device_count") or 0) if snap_check else 0
            if snap_check is not None and snap_check["keys_deleted_at"] is not None:
                await vless_provider.create_user(internal_user_id=internal_user_id, device_count=dc, expiry_days=period_days)
            elif snap_check is not None and snap_check["keys_deactivated_at"] is not None:
                await vless_provider.activate_user(internal_user_id=internal_user_id, device_count=dc, expiry_days=period_days)
            else:
                await vless_provider.create_user(internal_user_id=internal_user_id, device_count=dc, expiry_days=period_days)

            await pool.execute(
                """UPDATE subscription_snapshots
                   SET keys_deactivated_at = NULL, keys_deleted_at = NULL, updated_at = NOW()
                   WHERE internal_user_id = $1""",
                internal_user_id,
            )
            _LOGGER.info("fulfillment vless keys ensured user=%s", internal_user_id)
            return
        except Exception as exc:
            last_exc = exc
            _LOGGER.warning(
                "fulfillment vless key ensure failed user=%s attempt=%d/%d: %s",
                internal_user_id, attempt, max_retries, exc,
            )
            if attempt < max_retries:
                await _asyncio.sleep(retry_delay)

    _LOGGER.error(
        "fulfillment vless key ensure FAILED after %d retries user=%s — keys pending manual resolution",
        max_retries, internal_user_id, exc_info=last_exc,
    )

    # Notify the user that key creation is pending manual resolution
    if activation_notifier is not None and telegram_user_id is not None:
        await _send_activation_notice_best_effort(
            activation_notifier,
            telegram_user_id=telegram_user_id,
            text=(
                "✅ Оплата получена, подписка активирована!\n\n"
                "⏳ VPN-ключи создаются, это может занять несколько минут. "
                "Если ключи не появятся в течение часа, напишите в поддержку /support"
            ),
            reply_markup=None,
            correlation_id=correlation_id,
        )


async def _process_referral_commissions_best_effort(
    *,
    pool: asyncpg.Pool,
    payer_internal_user_id: str,
    payment_amount_kopecks: int,
    period_days: int,
    correlation_id: str,
) -> list[CreditedCommission]:
    """Credit referral commissions for a payment (idempotent by description) and RETURN the
    commissions that were actually credited, so the caller can best-effort notify each referrer.

    Only commissions whose idempotent transaction insert succeeded (`inserted=True`) are credited
    AND returned — a replayed payment yields no credits and an empty list (→ no notification).
    Any error is swallowed (best-effort) and an empty list returned so fulfillment never breaks."""
    try:
        from app.domain.referral import build_commissions_for_payment, resolve_direct_and_indirect_referrers
        from app.persistence.postgres_referral import (
            PostgresReferralBalanceRepository,
            PostgresReferralRelationshipRepository,
            PostgresReferralTransactionRepository,
        )
        from app.persistence.referral_contracts import ReferralTransactionRecord

        referrers = await PostgresReferralRelationshipRepository(pool).find_referrers(payer_internal_user_id)
        if not referrers:
            return []

        direct_referrer, indirect_referrer = resolve_direct_and_indirect_referrers(referrers)

        plan_id = _plan_id_from_period_days(period_days)
        commissions = build_commissions_for_payment(
            payer_user_id=payer_internal_user_id,
            direct_referrer_user_id=direct_referrer,
            indirect_referrer_user_id=indirect_referrer,
            plan_id=plan_id,
            payment_amount_kopecks=payment_amount_kopecks,
        )

        if not commissions:
            return []

        credited: list[CreditedCommission] = []
        async with pool.acquire() as conn, conn.transaction():
            for comm in commissions:
                dedup_desc = f"webhook:l{comm.level}:{comm.payer_user_id}:{comm.plan_id}:{payment_amount_kopecks}:{correlation_id}"
                tx_record = ReferralTransactionRecord(
                    transaction_id=f"ref-{uuid.uuid4()}",
                    internal_user_id=comm.referrer_user_id,
                    amount_kopecks=comm.amount_kopecks,
                    transaction_type="referral_credit",
                    related_user_id=comm.payer_user_id,
                    related_plan_id=comm.plan_id,
                    description=dedup_desc,
                    created_at=datetime.now(UTC),
                )
                inserted = await PostgresReferralTransactionRepository.append_transaction_if_description_absent_in_connection(conn, tx_record)
                if inserted:
                    await PostgresReferralBalanceRepository.credit_in_connection(conn, comm.referrer_user_id, comm.amount_kopecks)
                    credited.append(CreditedCommission(
                        referrer_internal_user_id=comm.referrer_user_id,
                        amount_kopecks=comm.amount_kopecks,
                        level=comm.level,
                    ))
        return credited
    except Exception:
        return []


async def _notify_referrers_of_commission_best_effort(
    *,
    pool: asyncpg.Pool,
    notifier: FulfillmentActivationTelegramNotifier,
    credited: list[CreditedCommission],
    correlation_id: str,
) -> None:
    """Best-effort: tell each newly-credited referrer that a referral paid and how much they earned.

    Skips referrers with no deliverable Telegram chat (web users → non-positive telegram id). The
    balance shown is read AFTER the credit commit and is therefore approximate under concurrent
    payouts to the same referrer — acceptable for a notification. Each referrer is notified
    independently (one failure never blocks another); all errors are swallowed. NB: reuses the
    generic activation notifier method (a plain send_text_message wrapper), with reply_markup=None."""
    from app.domain.referral import rubles_from_kopecks
    from app.persistence.postgres_referral import PostgresReferralBalanceRepository

    for comm in credited:
        try:
            tg_id = telegram_user_id_from_internal(comm.referrer_internal_user_id)
            if tg_id is None or tg_id <= 0:
                continue  # web user (negative id) — no Telegram chat to deliver to.
            balance_record = await PostgresReferralBalanceRepository(pool).get_balance(comm.referrer_internal_user_id)
            balance_kopecks = balance_record.balance_kopecks if balance_record is not None else 0
            earned = rubles_from_kopecks(comm.amount_kopecks)
            total = rubles_from_kopecks(balance_kopecks)
            who = "Ваш реферал оплатил подписку" if comm.level == 1 else "Реферал 2-го уровня оплатил подписку"
            text = (
                "💰 Реферальная программа\n\n"
                f"{who}. Вам начислено {earned:g} ₽ на баланс.\n"
                f"Текущий баланс: {total:g} ₽"
            )
            await notifier.send_subscription_activated_notice(
                telegram_user_id=tg_id,
                text=text,
                reply_markup=None,
                correlation_id=correlation_id,
            )
        except Exception:
            continue


async def process_fulfillment(
    *,
    pool: asyncpg.Pool,
    inp: FulfillmentInput,
    telemetry: FulfillmentTelemetry | None = None,
    activation_telegram_notifier: FulfillmentActivationTelegramNotifier | None = None,
    vless_provider: Any | None = None,
) -> FulfillmentResult:
    """Core fulfillment pipeline: UC-04 → UC-05 → snapshot → VLESS → referral → notification."""
    _telemetry = telemetry or NoopFulfillmentTelemetry()
    notify_activation = activation_telegram_notifier

    received_at = datetime.now(UTC)
    active_until_utc = inp.paid_at + timedelta(days=inp.period_days)
    correlation_id = f"fulfill-{uuid.uuid4()}"

    ingest_input = NormalizedBillingFactInput(
        billing_provider_key=inp.provider_key,
        external_event_id=inp.external_event_id,
        event_type=_EVENT_TYPE_SUBSCRIPTION_ACTIVATED,
        event_effective_at=inp.paid_at,
        event_received_at=received_at,
        status=BillingEventLedgerStatus.ACCEPTED,
        ingestion_correlation_id=correlation_id,
        internal_user_id=inp.internal_user_id,
        checkout_attempt_id=inp.external_payment_id,
        amount_currency=_ledger_amount_currency(inp.amount_kopecks),
        internal_fact_ref=None,
    )

    apply_result = None
    should_notify = False
    should_process_referral = False
    should_ensure_vless = False
    ref_amount_kopecks = 0

    try:
        identity_repo = PostgresUserIdentityRepository(pool)
        await identity_repo.create_if_absent(inp.telegram_user_id)

        # --- DB-only transaction: ingest + apply + snapshot (fast, no HTTP) ---
        async with pool.acquire() as conn, conn.transaction():
            # Read existing snapshot BEFORE apply (apply overwrites it)
            existing = await PostgresSubscriptionSnapshotReader.get_for_user_in_connection(
                conn, inp.internal_user_id,
            )

            atomic_ingest = PostgresAtomicBillingIngestion(pool)
            ingest_result = await atomic_ingest.ingest_in_connection(conn, ingest_input)
            apply = PostgresAtomicUC05SubscriptionApply(pool)
            apply_result = await apply.apply_in_connection(conn, ingest_result.record.internal_fact_ref)
            if apply_result.operation_outcome in (
                OperationOutcomeCategory.SUCCESS,
                OperationOutcomeCategory.IDEMPOTENT_NOOP,
            ):
                # Extend from current active_until if subscription is still active
                _LOGGER.info(
                    "fulfillment extend check user=%s existing=%s paid_at=%s",
                    inp.internal_user_id,
                    existing.active_until_utc.isoformat() if existing and existing.active_until_utc else None,
                    inp.paid_at.isoformat(),
                )
                if (
                    existing is not None
                    and existing.active_until_utc is not None
                    and existing.active_until_utc > inp.paid_at
                ):
                    extend_from = existing.active_until_utc
                    _LOGGER.info("fulfillment EXTENDING from existing end=%s", extend_from.isoformat())
                else:
                    extend_from = inp.paid_at
                active_until_utc = extend_from + timedelta(days=inp.period_days)
                _LOGGER.info("fulfillment new active_until=%s", active_until_utc.isoformat())

                snapshot_plan_id = _plan_id_from_period_days(inp.period_days)
                await PostgresSubscriptionSnapshotReader.upsert_state_in_connection(
                    conn,
                    SubscriptionSnapshot(
                        internal_user_id=inp.internal_user_id,
                        state_label="active",
                        active_until_utc=active_until_utc,
                        plan_id=snapshot_plan_id,
                        device_count=inp.device_count,
                    ),
                )
            is_new_active = (
                apply_result.operation_outcome is OperationOutcomeCategory.SUCCESS
                and not apply_result.idempotent_replay
                and apply_result.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED
            )
            if is_new_active:
                should_notify = notify_activation is not None
                should_process_referral = True
                should_ensure_vless = vless_provider is not None
                ref_plan_id = _plan_id_from_period_days(inp.period_days)
                ref_plan = get_plan(ref_plan_id)
                if inp.amount_kopecks is not None:
                    ref_amount_kopecks = inp.amount_kopecks
                else:
                    ref_amount_kopecks = ref_plan.price_rubles * 100 if ref_plan else 0

        # --- Post-transaction: HTTP calls (best-effort, won't block DB) ---
        if should_notify:
            plan = build_fulfillment_success_notification_plan(
                correlation_id=correlation_id,
                active_until_ymd=active_until_utc.date().isoformat(),
            )
            rendered = render_telegram_outbound_plan(plan)
            await _send_activation_notice_best_effort(
                notify_activation,
                telegram_user_id=inp.telegram_user_id,
                text=rendered.message_text,
                reply_markup=rendered.reply_markup,
                correlation_id=correlation_id,
            )
        if should_process_referral:
            credited = await _process_referral_commissions_best_effort(
                pool=pool,
                payer_internal_user_id=inp.internal_user_id,
                payment_amount_kopecks=ref_amount_kopecks,
                period_days=inp.period_days,
                correlation_id=correlation_id,
            )
            if credited and notify_activation is not None:
                await _notify_referrers_of_commission_best_effort(
                    pool=pool,
                    notifier=notify_activation,
                    credited=credited,
                    correlation_id=correlation_id,
                )
        if should_ensure_vless:
            await _ensure_vless_keys_after_payment(
                pool=pool,
                vless_provider=vless_provider,
                internal_user_id=inp.internal_user_id,
                telegram_user_id=inp.telegram_user_id,
                activation_notifier=notify_activation,
                correlation_id=correlation_id,
                period_days=inp.period_days,
            )
    except Exception:
        await _telemetry.emit(decision="rejected", reason_bucket="dependency_failure")
        raise

    if apply_result is None:
        await _telemetry.emit(decision="rejected", reason_bucket="apply_failed")
        return FulfillmentResult(
            operation_outcome=OperationOutcomeCategory.INTERNAL_FAILURE,
            idempotent_replay=False,
            apply_outcome=None,
            correlation_id=correlation_id,
        )

    if apply_result.operation_outcome not in (
        OperationOutcomeCategory.SUCCESS,
        OperationOutcomeCategory.IDEMPOTENT_NOOP,
    ):
        await _telemetry.emit(decision="rejected", reason_bucket="apply_failed")
    else:
        await _telemetry.emit(decision="accepted", reason_bucket="applied")

    return FulfillmentResult(
        operation_outcome=apply_result.operation_outcome,
        idempotent_replay=apply_result.idempotent_replay,
        apply_outcome=apply_result.apply_outcome,
        correlation_id=correlation_id,
    )
