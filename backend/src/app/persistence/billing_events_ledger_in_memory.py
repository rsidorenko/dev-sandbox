"""In-memory implementation of BillingEventsLedgerRepository for tests and local composition."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from app.persistence.billing_events_ledger_contracts import (
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
    BillingEventsLedgerRepository,
    BillingEventsLedgerUserSummary,
    BillingFactsPresenceCategory,
)


class InMemoryBillingEventsLedgerRepository(BillingEventsLedgerRepository):
    """Append-only in-memory ledger; safe for tests and local wiring only.

    Idempotent with respect to (billing_provider_key, external_event_id).
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: list[BillingEventLedgerRecord] = []
        self._by_provider_and_external_id: dict[tuple[str, str], BillingEventLedgerRecord] = {}
        self._by_internal_fact_ref: dict[str, BillingEventLedgerRecord] = {}

    async def append_or_get_by_provider_and_external_id(
        self,
        record: BillingEventLedgerRecord,
    ) -> BillingEventLedgerRecord:
        key = (record.billing_provider_key, record.external_event_id)
        async with self._lock:
            existing = self._by_provider_and_external_id.get(key)
            if existing is not None:
                return existing

            self._records.append(record)
            self._by_provider_and_external_id[key] = record
            self._by_internal_fact_ref[record.internal_fact_ref] = record
            return record

    async def get_by_internal_fact_ref(
        self,
        internal_fact_ref: str,
    ) -> BillingEventLedgerRecord | None:
        async with self._lock:
            return self._by_internal_fact_ref.get(internal_fact_ref)

    async def get_user_billing_facts_summary(
        self,
        internal_user_id: str,
    ) -> BillingEventsLedgerUserSummary:
        async with self._lock:
            # Only accepted facts are surfaced in the summary for now.
            accepted_for_user: tuple[BillingEventLedgerRecord, ...] = tuple(
                r
                for r in self._iter_records_locked()
                if r.internal_user_id == internal_user_id and r.status is BillingEventLedgerStatus.ACCEPTED
            )

        if not accepted_for_user:
            return BillingEventsLedgerUserSummary(
                category=BillingFactsPresenceCategory.NONE,
                internal_fact_refs=(),
            )

        return BillingEventsLedgerUserSummary(
            category=BillingFactsPresenceCategory.HAS_ACCEPTED,
            internal_fact_refs=tuple(r.internal_fact_ref for r in accepted_for_user),
        )

    async def records_for_tests(self) -> tuple[BillingEventLedgerRecord, ...]:
        """Test-only helper to observe append-only behaviour."""
        async with self._lock:
            return tuple(self._records)

    def _iter_records_locked(self) -> Iterable[BillingEventLedgerRecord]:
        # Internal helper: assumes caller holds _lock.
        return tuple(self._records)
