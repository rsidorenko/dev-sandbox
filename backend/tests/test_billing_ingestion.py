"""Tests for internal normalized billing fact ingestion (application layer)."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime

import pytest

from app.admin_support import Adm02BillingFactsCategory, Adm02BillingFactsLedgerReadAdapter
from app.application.billing_ingestion import (
    IngestNormalizedBillingFactHandler,
    NormalizedBillingFactInput,
)
from app.persistence import (
    BILLING_INGESTION_AUDIT_OPERATION,
    BILLING_INGESTION_OUTCOME_ACCEPTED,
    BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY,
    BillingEventAmountCurrency,
    BillingEventLedgerStatus,
    InMemoryBillingEventsLedgerRepository,
    InMemoryBillingIngestionAuditAppender,
)
from app.persistence.billing_ingestion_audit_contracts import BillingIngestionAuditRecord
from app.security.errors import InternalErrorCategory, PersistenceDependencyError
from app.security.validation import ValidationError

_NOW = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
_LATER = datetime(2026, 1, 15, 10, 31, 0, tzinfo=UTC)


def _base_input(**overrides) -> NormalizedBillingFactInput:
    params = {
        "billing_provider_key": "p_key",
        "external_event_id": "ext-1",
        "event_type": "payment_succeeded",
        "event_effective_at": _NOW,
        "event_received_at": _LATER,
        "status": BillingEventLedgerStatus.ACCEPTED,
        "ingestion_correlation_id": "corr-a",
        "internal_user_id": "user-1",
        "amount_currency": BillingEventAmountCurrency(amount_minor_units=100, currency_code="USD"),
    }
    params.update(overrides)
    return NormalizedBillingFactInput(**params)


def _handler(
    repo: InMemoryBillingEventsLedgerRepository,
    audit: InMemoryBillingIngestionAuditAppender | None = None,
) -> tuple[IngestNormalizedBillingFactHandler, InMemoryBillingIngestionAuditAppender]:
    a = audit or InMemoryBillingIngestionAuditAppender()
    return IngestNormalizedBillingFactHandler(repo, a), a


def test_dto_has_no_raw_provider_field() -> None:
    names = {f.name for f in fields(NormalizedBillingFactInput)}
    assert "raw_provider_payload" not in names
    assert "raw_payload" not in names


@pytest.mark.asyncio
async def test_accepts_and_appends_normalized_fact() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    handler, audit = _handler(repo)
    inp = _base_input(internal_fact_ref="fact-ref-1")

    result = await handler.handle(inp)

    assert result.is_idempotent_replay is False
    assert result.record.internal_fact_ref == "fact-ref-1"
    assert result.record.billing_provider_key == "p_key"
    all_rows = await repo.records_for_tests()
    assert len(all_rows) == 1
    assert all_rows[0] is result.record
    arows = await audit.records_for_tests()
    assert len(arows) == 1
    r = arows[0]
    assert r.internal_fact_ref == "fact-ref-1"
    assert r.operation == BILLING_INGESTION_AUDIT_OPERATION
    assert r.outcome == BILLING_INGESTION_OUTCOME_ACCEPTED
    assert r.is_idempotent_replay is False
    assert r.billing_event_status == BillingEventLedgerStatus.ACCEPTED.value
    assert r.billing_provider_key == result.record.billing_provider_key
    assert r.external_event_id == result.record.external_event_id
    assert r.ingestion_correlation_id == result.record.ingestion_correlation_id


@pytest.mark.asyncio
async def test_duplicate_provider_external_returns_original_does_not_overwrite() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    handler, audit = _handler(repo)

    first = await handler.handle(_base_input())
    assert first.is_idempotent_replay is False

    second = await handler.handle(_base_input())

    assert second.is_idempotent_replay is True
    assert second.record is first.record
    assert first.record.internal_fact_ref == second.record.internal_fact_ref
    all_rows = await repo.records_for_tests()
    assert len(all_rows) == 1
    arows = await audit.records_for_tests()
    assert len(arows) == 2
    assert arows[0].outcome == BILLING_INGESTION_OUTCOME_ACCEPTED
    assert arows[0].is_idempotent_replay is False
    assert arows[1].outcome == BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY
    assert arows[1].is_idempotent_replay is True
    assert arows[0].internal_fact_ref == arows[1].internal_fact_ref
    assert arows[0].ingestion_correlation_id == arows[1].ingestion_correlation_id


@pytest.mark.asyncio
async def test_audit_ignored_status_uses_ledger_billing_event_status() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    handler, audit = _handler(repo)
    await handler.handle(
        _base_input(
            status=BillingEventLedgerStatus.IGNORED,
            internal_fact_ref="ig-1",
            external_event_id="ev-ig-2",
        )
    )
    arows = await audit.records_for_tests()
    assert len(arows) == 1
    assert arows[0].billing_event_status == BillingEventLedgerStatus.IGNORED.value
    assert arows[0].outcome == BILLING_INGESTION_OUTCOME_ACCEPTED


@pytest.mark.asyncio
async def test_audit_duplicate_ledger_status_uses_billing_event_status() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    handler, audit = _handler(repo)
    await handler.handle(
        _base_input(
            status=BillingEventLedgerStatus.DUPLICATE,
            internal_fact_ref="ld-1",
            external_event_id="ev-ld-2",
        )
    )
    arows = await audit.records_for_tests()
    assert len(arows) == 1
    assert arows[0].billing_event_status == BillingEventLedgerStatus.DUPLICATE.value
    assert arows[0].outcome == BILLING_INGESTION_OUTCOME_ACCEPTED


class _FailingBillingIngestionAuditAppender:
    """Appends on second call: first succeeds, second raises (fail-closed to caller)."""

    def __init__(self) -> None:
        self._inner = InMemoryBillingIngestionAuditAppender()
        self.n_append = 0

    async def append(self, record: BillingIngestionAuditRecord) -> None:
        self.n_append += 1
        if self.n_append == 1:
            await self._inner.append(record)
        else:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT)

    @property
    def inner(self) -> InMemoryBillingIngestionAuditAppender:
        return self._inner


@pytest.mark.asyncio
async def test_validation_failure_does_not_write_ledger_or_audit() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    handler, audit = _handler(repo)
    with pytest.raises(ValidationError):
        await handler.handle(_base_input(billing_provider_key="  "))
    assert await repo.records_for_tests() == ()
    assert await audit.records_for_tests() == ()


@pytest.mark.asyncio
async def test_audit_persistence_failure_after_ledger_open_raises() -> None:
    """Second ingest: ledger idempotent hit succeeds; audit failure is observable (no handler result)."""
    repo = InMemoryBillingEventsLedgerRepository()
    fail = _FailingBillingIngestionAuditAppender()
    handler = IngestNormalizedBillingFactHandler(repo, fail)
    r1 = await handler.handle(_base_input(external_event_id="ext-unique-1"))
    assert r1.is_idempotent_replay is False
    assert len(await fail.inner.records_for_tests()) == 1
    with pytest.raises(PersistenceDependencyError):
        await handler.handle(_base_input(external_event_id="ext-unique-1", ingestion_correlation_id="c2"))
    all_rows = await repo.records_for_tests()
    assert len(all_rows) == 1
    ar = await fail.inner.records_for_tests()
    assert len(ar) == 1
    assert fail.n_append == 2


@pytest.mark.asyncio
async def test_adm02_has_accepted_after_accepted_ingest() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    handler, _ = _handler(repo)
    adapter = Adm02BillingFactsLedgerReadAdapter(repo)
    user_id = "u-adm-1"
    ref = "ledger-ref-1"
    await handler.handle(
        _base_input(
            internal_user_id=user_id,
            internal_fact_ref=ref,
            external_event_id="evt-aa",
        )
    )
    d = await adapter.get_billing_facts_diagnostics(user_id)
    assert d.category is Adm02BillingFactsCategory.HAS_ACCEPTED
    assert ref in d.internal_fact_refs


@pytest.mark.asyncio
async def test_adm02_ignores_ignored_status_for_has_accepted() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    handler, _ = _handler(repo)
    adapter = Adm02BillingFactsLedgerReadAdapter(repo)
    user_id = "u-ign"
    await handler.handle(
        _base_input(
            internal_user_id=user_id,
            status=BillingEventLedgerStatus.IGNORED,
            internal_fact_ref="ignored-1",
            external_event_id="ev-ig",
        )
    )
    d = await adapter.get_billing_facts_diagnostics(user_id)
    assert d.category is Adm02BillingFactsCategory.NONE
    assert d.internal_fact_refs == ()


@pytest.mark.asyncio
async def test_adm02_duplicate_status_not_counted_as_accepted() -> None:
    """Ledger may store a DUPLICATE-labeled fact; ADM-02 only surfaces ACCEPTED."""
    repo = InMemoryBillingEventsLedgerRepository()
    handler, _ = _handler(repo)
    adapter = Adm02BillingFactsLedgerReadAdapter(repo)
    user_id = "u-dup"
    await handler.handle(
        _base_input(
            internal_user_id=user_id,
            status=BillingEventLedgerStatus.DUPLICATE,
            internal_fact_ref="dup-1",
            external_event_id="ev-dup",
        )
    )
    d = await adapter.get_billing_facts_diagnostics(user_id)
    assert d.category is Adm02BillingFactsCategory.NONE
    assert d.internal_fact_refs == ()


@pytest.mark.asyncio
async def test_rejects_empty_required_string() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    handler, _ = _handler(repo)
    base = _base_input()
    for bad in ("billing_provider_key", "external_event_id", "event_type", "ingestion_correlation_id"):
        with pytest.raises(ValidationError):
            await handler.handle(replace(base, **{bad: "  "}))


@pytest.mark.asyncio
async def test_rejects_naive_datetimes() -> None:
    repo = InMemoryBillingEventsLedgerRepository()
    handler, _ = _handler(repo)
    naive = datetime(2026, 1, 1, 12, 0, 0)
    with pytest.raises(ValidationError):
        await handler.handle(_base_input(event_effective_at=naive))
    with pytest.raises(ValidationError):
        await handler.handle(_base_input(event_received_at=naive, event_effective_at=_NOW))
