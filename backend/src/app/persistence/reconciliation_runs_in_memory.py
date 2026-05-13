"""In-memory implementation of ReconciliationRunsRepository for tests and local composition."""

from __future__ import annotations

import asyncio

from app.persistence.reconciliation_runs_contracts import (
    ReconciliationRunOutcome,
    ReconciliationRunRecord,
    ReconciliationRunsRepository,
    ReconciliationRunUserSummary,
)


class InMemoryReconciliationRunsRepository(ReconciliationRunsRepository):
    """Append-only in-memory store; safe for tests and local wiring only."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: list[ReconciliationRunRecord] = []

    async def append_run_record(
        self,
        record: ReconciliationRunRecord,
    ) -> ReconciliationRunRecord:
        async with self._lock:
            self._records.append(record)
            return record

    async def get_user_reconciliation_summary(
        self,
        internal_user_id: str,
    ) -> ReconciliationRunUserSummary:
        async with self._lock:
            for_user = tuple(r for r in self._records if r.internal_user_id == internal_user_id)

        if not for_user:
            return ReconciliationRunUserSummary(
                last_run_marker=ReconciliationRunOutcome.UNKNOWN,
            )

        newest = max(for_user, key=lambda r: r.started_at)
        return ReconciliationRunUserSummary(last_run_marker=newest.outcome)

    async def records_for_tests(self) -> tuple[ReconciliationRunRecord, ...]:
        """Test-only helper to observe append-only behaviour."""
        async with self._lock:
            return tuple(self._records)
