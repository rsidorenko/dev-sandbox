from __future__ import annotations

from dataclasses import is_dataclass
from datetime import UTC, datetime

from app.persistence.billing_events_ledger_contracts import (
    BillingEventAmountCurrency,
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
    BillingEventsLedgerRepository,
    BillingEventsLedgerUserSummary,
    BillingFactsPresenceCategory,
)


def test_billing_events_ledger_record_dataclass_shape() -> None:
    amount = BillingEventAmountCurrency(
        amount_minor_units=1234,
        currency_code="USD",
    )

    record = BillingEventLedgerRecord(
        internal_fact_ref="be-1",
        billing_provider_key="provider_a",
        external_event_id="ext-evt-1",
        event_type="payment_succeeded",
        event_effective_at=datetime.now(UTC),
        event_received_at=datetime.now(UTC),
        internal_user_id="user-1",
        checkout_attempt_id="chk-1",
        amount_currency=amount,
        status=BillingEventLedgerStatus.ACCEPTED,
        ingestion_correlation_id="corr-1",
    )

    assert is_dataclass(record)
    assert record.internal_fact_ref == "be-1"
    assert record.amount_currency is amount
    assert record.status is BillingEventLedgerStatus.ACCEPTED


def test_billing_events_ledger_user_summary_shape() -> None:
    summary = BillingEventsLedgerUserSummary(
        category=BillingFactsPresenceCategory.HAS_ACCEPTED,
        internal_fact_refs=("be-1", "be-2"),
    )

    assert is_dataclass(summary)
    assert summary.category is BillingFactsPresenceCategory.HAS_ACCEPTED
    assert summary.internal_fact_refs == ("be-1", "be-2")


def test_billing_events_ledger_repository_protocol_surface() -> None:
    # Protocol itself is not instantiated; we only assert method presence and signatures exist.
    assert hasattr(
        BillingEventsLedgerRepository,
        "append_or_get_by_provider_and_external_id",
    )
    assert hasattr(
        BillingEventsLedgerRepository,
        "get_user_billing_facts_summary",
    )
