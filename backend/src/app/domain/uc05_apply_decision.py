"""Чистое решение UC-05 apply (без I/O) для переходов снапшота подписки."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.billing_apply_rules import (
    UC05_ALLOWLISTED_EVENT_TYPES,
    UC05_NO_USER_SENTINEL,
)
from app.persistence.billing_events_ledger_contracts import (
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
)
from app.persistence.billing_subscription_apply_contracts import (
    BillingSubscriptionApplyOutcome,
    BillingSubscriptionApplyReason,
)
from app.shared.types import SubscriptionSnapshotState


class UC05ApplyPath(StrEnum):
    """Результирующий путь перед персистентной записью (обработчик маппит на исходы операций)."""

    FACT_NOT_FOUND = "fact_not_found"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    PERSIST = "persist"


@dataclass(frozen=True, slots=True)
class UC05PersistInstruction:
    """Долговременная работа для первого применения данного internal_fact_ref."""

    internal_fact_ref: str
    # Строка в billing_subscription_apply_records (столбец NOT NULL; может быть сентинелем)
    record_internal_user_id: str
    apply_outcome: BillingSubscriptionApplyOutcome
    reason: BillingSubscriptionApplyReason
    # Если установлено, выполнить upsert subscription_snapshots с этим state_label (значение UserSnapshotState)
    snapshot_state_label: str | None
    # Аудит: внутренний пользователь из ledger (опционально)
    audit_internal_user_id: str | None
    billing_provider_key: str
    external_event_id: str
    event_type: str
    billing_event_status: str


def first_time_decision(
    fact: BillingEventLedgerRecord,
) -> UC05PersistInstruction:
    """Вычисляет долговременное применение для факта, которого ещё нет в хранилище идемпотентности.

    Предусловие: вызывающий подтвердил, что строки идемпотентности для ``fact.internal_fact_ref`` ещё нет.
    """
    st = fact.status
    if st is not BillingEventLedgerStatus.ACCEPTED:
        return UC05PersistInstruction(
            internal_fact_ref=fact.internal_fact_ref,
            record_internal_user_id=UC05_NO_USER_SENTINEL if fact.internal_user_id is None else fact.internal_user_id,
            apply_outcome=BillingSubscriptionApplyOutcome.NO_ACTIVATION,
            reason=BillingSubscriptionApplyReason.LEDGER_STATUS_NOT_ACCEPTED,
            snapshot_state_label=None,
            audit_internal_user_id=fact.internal_user_id,
            billing_provider_key=fact.billing_provider_key,
            external_event_id=fact.external_event_id,
            event_type=fact.event_type,
            billing_event_status=st.value,
        )

    if not fact.internal_user_id:
        return UC05PersistInstruction(
            internal_fact_ref=fact.internal_fact_ref,
            record_internal_user_id=UC05_NO_USER_SENTINEL,
            apply_outcome=BillingSubscriptionApplyOutcome.NEEDS_REVIEW,
            reason=BillingSubscriptionApplyReason.MISSING_INTERNAL_USER,
            snapshot_state_label=None,
            audit_internal_user_id=None,
            billing_provider_key=fact.billing_provider_key,
            external_event_id=fact.external_event_id,
            event_type=fact.event_type,
            billing_event_status=st.value,
        )

    uid = fact.internal_user_id
    if fact.event_type not in UC05_ALLOWLISTED_EVENT_TYPES:
        return UC05PersistInstruction(
            internal_fact_ref=fact.internal_fact_ref,
            record_internal_user_id=uid,
            apply_outcome=BillingSubscriptionApplyOutcome.NEEDS_REVIEW,
            reason=BillingSubscriptionApplyReason.UNKNOWN_EVENT_TYPE,
            snapshot_state_label=SubscriptionSnapshotState.NEEDS_REVIEW.value,
            audit_internal_user_id=uid,
            billing_provider_key=fact.billing_provider_key,
            external_event_id=fact.external_event_id,
            event_type=fact.event_type,
            billing_event_status=st.value,
        )

    return UC05PersistInstruction(
        internal_fact_ref=fact.internal_fact_ref,
        record_internal_user_id=uid,
        apply_outcome=BillingSubscriptionApplyOutcome.ACTIVE_APPLIED,
        reason=BillingSubscriptionApplyReason.OK,
        snapshot_state_label=SubscriptionSnapshotState.ACTIVE.value,
        audit_internal_user_id=uid,
        billing_provider_key=fact.billing_provider_key,
        external_event_id=fact.external_event_id,
        event_type=fact.event_type,
        billing_event_status=st.value,
    )
