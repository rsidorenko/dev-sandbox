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
from app.persistence.billing_events_ledger_contracts import BillingEventLedgerStatus
from app.persistence.billing_subscription_apply_contracts import BillingSubscriptionApplyOutcome
from app.persistence.postgres_billing_ingestion_atomic import PostgresAtomicBillingIngestion
from app.persistence.postgres_billing_subscription_apply import PostgresAtomicUC05SubscriptionApply
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository
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


@dataclass(frozen=True, slots=True)
class FulfillmentResult:
    operation_outcome: OperationOutcomeCategory
    idempotent_replay: bool
    apply_outcome: BillingSubscriptionApplyOutcome | None
    correlation_id: str


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
) -> None:
    from app.issuance.vless_provider import VlessProviderOutcome

    try:
        snap_check = await pool.fetchrow(
            """SELECT keys_deactivated_at, keys_deleted_at FROM subscription_snapshots
               WHERE internal_user_id = $1""",
            internal_user_id,
        )
        if snap_check is not None and snap_check["keys_deleted_at"] is not None:
            await vless_provider.create_user(internal_user_id=internal_user_id)
        elif snap_check is not None and snap_check["keys_deactivated_at"] is not None:
            await vless_provider.activate_user(internal_user_id=internal_user_id)
        else:
            await vless_provider.create_user(internal_user_id=internal_user_id)

        await pool.execute(
            """UPDATE subscription_snapshots
               SET keys_deactivated_at = NULL, keys_deleted_at = NULL, updated_at = NOW()
               WHERE internal_user_id = $1""",
            internal_user_id,
        )
        _LOGGER.info("fulfillment vless keys ensured user=%s", internal_user_id)
    except Exception:
        _LOGGER.warning("fulfillment vless key ensure failed user=%s", internal_user_id, exc_info=True)


async def _process_referral_commissions_best_effort(
    *,
    pool: asyncpg.Pool,
    payer_internal_user_id: str,
    payment_amount_kopecks: int,
    period_days: int,
    correlation_id: str,
) -> None:
    try:
        from app.domain.referral import build_commissions_for_payment, resolve_direct_and_indirect_referrers
        from app.persistence.postgres_referral import (
            PostgresReferralBalanceRepository,
            PostgresReferralRelationshipRepository,
            PostgresReferralTransactionRepository,
        )
        from app.persistence.referral_contracts import ReferralTransactionRecord

        rel_repo = PostgresReferralRelationshipRepository(pool)
        bal_repo = PostgresReferralBalanceRepository(pool)
        tx_repo = PostgresReferralTransactionRepository(pool)

        referrers = await rel_repo.find_referrers(payer_internal_user_id)
        if not referrers:
            return

        direct_referrer, indirect_referrer = resolve_direct_and_indirect_referrers(referrers)

        plan_id = _plan_id_from_period_days(period_days)
        commissions = build_commissions_for_payment(
            payer_user_id=payer_internal_user_id,
            direct_referrer_user_id=direct_referrer,
            indirect_referrer_user_id=indirect_referrer,
            plan_id=plan_id,
            payment_amount_kopecks=payment_amount_kopecks,
        )

        for comm in commissions:
            dedup_desc = f"webhook:l{comm.level}:{comm.payer_user_id}:{comm.plan_id}:{payment_amount_kopecks}"
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
            inserted = await tx_repo.append_transaction_if_description_absent(tx_record)
            if inserted:
                await bal_repo.credit(comm.referrer_user_id, comm.amount_kopecks)
    except Exception:
        return


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
        amount_currency=None,
        internal_fact_ref=None,
    )

    apply_result = None
    try:
        identity_repo = PostgresUserIdentityRepository(pool)
        await identity_repo.create_if_absent(inp.telegram_user_id)

        async with pool.acquire() as conn, conn.transaction():
            atomic_ingest = PostgresAtomicBillingIngestion(pool)
            ingest_result = await atomic_ingest.ingest_in_connection(conn, ingest_input)
            apply = PostgresAtomicUC05SubscriptionApply(pool)
            apply_result = await apply.apply_in_connection(conn, ingest_result.record.internal_fact_ref)
            if apply_result.operation_outcome in (
                OperationOutcomeCategory.SUCCESS,
                OperationOutcomeCategory.IDEMPOTENT_NOOP,
            ):
                snapshot_plan_id = _plan_id_from_period_days(inp.period_days)
                await PostgresSubscriptionSnapshotReader.upsert_state_in_connection(
                    conn,
                    SubscriptionSnapshot(
                        internal_user_id=inp.internal_user_id,
                        state_label="active",
                        active_until_utc=active_until_utc,
                        plan_id=snapshot_plan_id,
                        device_count=DEFAULT_DEVICE_LIMIT,
                    ),
                )
            # Telegram notification
            if (
                notify_activation is not None
                and apply_result.operation_outcome is OperationOutcomeCategory.SUCCESS
                and not apply_result.idempotent_replay
                and apply_result.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED
            ):
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
            # Referral commissions
            if (
                apply_result.operation_outcome is OperationOutcomeCategory.SUCCESS
                and not apply_result.idempotent_replay
                and apply_result.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED
            ):
                ref_plan_id = _plan_id_from_period_days(inp.period_days)
                ref_plan = get_plan(ref_plan_id)
                if inp.amount_kopecks is not None:
                    ref_amount_kopecks = inp.amount_kopecks
                else:
                    ref_amount_kopecks = ref_plan.price_rubles * 100 if ref_plan else 0
                await _process_referral_commissions_best_effort(
                    pool=pool,
                    payer_internal_user_id=inp.internal_user_id,
                    payment_amount_kopecks=ref_amount_kopecks,
                    period_days=inp.period_days,
                    correlation_id=correlation_id,
                )
            # VLESS key lifecycle
            if (
                vless_provider is not None
                and apply_result.operation_outcome is OperationOutcomeCategory.SUCCESS
                and not apply_result.idempotent_replay
                and apply_result.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED
            ):
                await _ensure_vless_keys_after_payment(
                    pool=pool,
                    vless_provider=vless_provider,
                    internal_user_id=inp.internal_user_id,
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
