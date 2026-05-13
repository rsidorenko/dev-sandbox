from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.admin_support import (
    Adm02BillingFactsCategory,
    Adm02BillingFactsDiagnostics,
    Adm02BillingFactsLedgerReadAdapter,
)
from app.persistence import (
    BillingEventAmountCurrency,
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
    BillingEventsLedgerRepository,
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
async def test_adapter_returns_none_category_and_empty_refs_for_user_without_accepted_facts() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    adapter = Adm02BillingFactsLedgerReadAdapter(repo)

    diagnostics = await adapter.get_billing_facts_diagnostics("missing-user")

    assert isinstance(diagnostics, Adm02BillingFactsDiagnostics)
    assert diagnostics.category is Adm02BillingFactsCategory.NONE
    assert diagnostics.internal_fact_refs == ()


@pytest.mark.asyncio
async def test_adapter_returns_has_accepted_and_expected_refs_for_user_with_accepted_facts() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    adapter = Adm02BillingFactsLedgerReadAdapter(repo)
    user_id = "user-1"

    await repo.append_or_get_by_provider_and_external_id(
        _make_record(
            internal_fact_ref="be-1",
            internal_user_id=user_id,
            external_event_id="evt-1",
        )
    )
    await repo.append_or_get_by_provider_and_external_id(
        _make_record(
            internal_fact_ref="be-2",
            internal_user_id=user_id,
            external_event_id="evt-2",
        )
    )

    diagnostics = await adapter.get_billing_facts_diagnostics(user_id)

    assert diagnostics.category is Adm02BillingFactsCategory.HAS_ACCEPTED
    assert diagnostics.internal_fact_refs == ("be-1", "be-2")


class _UnknownSummaryRepo(BillingEventsLedgerRepository):
    async def append_or_get_by_provider_and_external_id(self, record):  # type: ignore[override]
        raise NotImplementedError

    async def get_user_billing_facts_summary(self, internal_user_id: str) -> BillingEventsLedgerUserSummary:  # type: ignore[override]
        return BillingEventsLedgerUserSummary(
            category=BillingFactsPresenceCategory.UNKNOWN,
            internal_fact_refs=("be-unknown-1",),
        )


@pytest.mark.asyncio
async def test_adapter_propagates_unknown_category() -> None:
    repo = _UnknownSummaryRepo()
    adapter = Adm02BillingFactsLedgerReadAdapter(repo)

    diagnostics = await adapter.get_billing_facts_diagnostics("user-unknown")

    assert diagnostics.category is Adm02BillingFactsCategory.UNKNOWN
    assert diagnostics.internal_fact_refs == ("be-unknown-1",)


class _FailingSummaryRepo(BillingEventsLedgerRepository):
    async def append_or_get_by_provider_and_external_id(self, record):  # type: ignore[override]
        raise NotImplementedError

    async def get_user_billing_facts_summary(self, internal_user_id: str) -> BillingEventsLedgerUserSummary:  # type: ignore[override]
        raise RuntimeError("ledger failure")


@pytest.mark.asyncio
async def test_adapter_does_not_swallow_repository_exceptions() -> None:
    repo = _FailingSummaryRepo()
    adapter = Adm02BillingFactsLedgerReadAdapter(repo)

    with pytest.raises(RuntimeError):
        await adapter.get_billing_facts_diagnostics("user-error")
