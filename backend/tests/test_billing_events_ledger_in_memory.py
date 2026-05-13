from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.persistence import (
    BillingEventAmountCurrency,
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
    BillingEventsLedgerUserSummary,
    BillingFactsPresenceCategory,
    InMemoryBillingEventsLedgerRepository,
)


def _make_record(
    *,
    internal_fact_ref: str,
    billing_provider_key: str = "provider_a",
    external_event_id: str = "ext-evt-1",
    internal_user_id: str | None = "user-1",
    status: BillingEventLedgerStatus = BillingEventLedgerStatus.ACCEPTED,
) -> BillingEventLedgerRecord:
    now = datetime.now(UTC)
    return BillingEventLedgerRecord(
        internal_fact_ref=internal_fact_ref,
        billing_provider_key=billing_provider_key,
        external_event_id=external_event_id,
        event_type="payment_succeeded",
        event_effective_at=now,
        event_received_at=now,
        internal_user_id=internal_user_id,
        checkout_attempt_id=None,
        amount_currency=BillingEventAmountCurrency(
            amount_minor_units=1000,
            currency_code="USD",
        ),
        status=status,
        ingestion_correlation_id="corr-1",
    )


@pytest.mark.asyncio
async def test_append_new_record_and_return() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    record = _make_record(internal_fact_ref="be-1")

    stored = await repo.append_or_get_by_provider_and_external_id(record)

    assert stored is record
    all_records = await repo.records_for_tests()
    assert len(all_records) == 1
    assert all_records[0] is record


@pytest.mark.asyncio
async def test_append_same_provider_and_external_id_is_idempotent() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    first = _make_record(internal_fact_ref="be-1", external_event_id="ext-evt-1")
    second = _make_record(internal_fact_ref="be-2", external_event_id="ext-evt-1")

    stored_first = await repo.append_or_get_by_provider_and_external_id(first)
    stored_second = await repo.append_or_get_by_provider_and_external_id(second)

    assert stored_first is stored_second
    all_records = await repo.records_for_tests()
    assert len(all_records) == 1
    assert all_records[0] is stored_first


@pytest.mark.asyncio
async def test_summary_for_user_without_records_is_none() -> None:
    repo = InMemoryBillingEventsLedgerRepository()

    summary = await repo.get_user_billing_facts_summary("missing-user")

    assert isinstance(summary, BillingEventsLedgerUserSummary)
    assert summary.category is BillingFactsPresenceCategory.NONE
    assert summary.internal_fact_refs == ()


@pytest.mark.asyncio
async def test_summary_for_user_with_accepted_records_has_accepted() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    user_id = "user-1"
    other_user_id = "user-2"

    await repo.append_or_get_by_provider_and_external_id(
        _make_record(
            internal_fact_ref="be-1",
            internal_user_id=user_id,
            external_event_id="evt-1",
            status=BillingEventLedgerStatus.ACCEPTED,
        )
    )
    await repo.append_or_get_by_provider_and_external_id(
        _make_record(
            internal_fact_ref="be-2",
            internal_user_id=user_id,
            external_event_id="evt-2",
            status=BillingEventLedgerStatus.ACCEPTED,
        )
    )
    await repo.append_or_get_by_provider_and_external_id(
        _make_record(
            internal_fact_ref="be-3",
            internal_user_id=other_user_id,
            external_event_id="evt-3",
            status=BillingEventLedgerStatus.ACCEPTED,
        )
    )

    summary = await repo.get_user_billing_facts_summary(user_id)

    assert summary.category is BillingFactsPresenceCategory.HAS_ACCEPTED
    assert summary.internal_fact_refs == ("be-1", "be-2")


@pytest.mark.asyncio
async def test_summary_ignores_non_accepted_status_for_now() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    user_id = "user-1"

    await repo.append_or_get_by_provider_and_external_id(
        _make_record(
            internal_fact_ref="be-ignored-1",
            internal_user_id=user_id,
            external_event_id="evt-ign-1",
            status=BillingEventLedgerStatus.IGNORED,
        )
    )
    await repo.append_or_get_by_provider_and_external_id(
        _make_record(
            internal_fact_ref="be-accepted-1",
            internal_user_id=user_id,
            external_event_id="evt-acc-1",
            status=BillingEventLedgerStatus.ACCEPTED,
        )
    )

    summary = await repo.get_user_billing_facts_summary(user_id)

    assert summary.category is BillingFactsPresenceCategory.HAS_ACCEPTED
    assert summary.internal_fact_refs == ("be-accepted-1",)


@pytest.mark.asyncio
async def test_append_is_append_only_via_test_helper() -> None:
    repo = InMemoryBillingEventsLedgerRepository()

    await repo.append_or_get_by_provider_and_external_id(
        _make_record(internal_fact_ref="be-1", external_event_id="evt-1")
    )
    await repo.append_or_get_by_provider_and_external_id(
        _make_record(internal_fact_ref="be-2", external_event_id="evt-2")
    )

    records = await repo.records_for_tests()
    assert [r.internal_fact_ref for r in records] == ["be-1", "be-2"]


@pytest.mark.asyncio
async def test_get_by_internal_fact_ref() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    r = _make_record(internal_fact_ref="ref-a", external_event_id="e-a")
    await repo.append_or_get_by_provider_and_external_id(r)
    assert await repo.get_by_internal_fact_ref("ref-a") is r
    assert await repo.get_by_internal_fact_ref("missing") is None
