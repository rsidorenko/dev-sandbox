"""In-memory implementation of MismatchQuarantineRepository for tests and local composition."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from app.persistence.mismatch_quarantine_contracts import (
    MismatchQuarantineReasonCode,
    MismatchQuarantineRecord,
    MismatchQuarantineRepository,
    MismatchQuarantineResolutionStatus,
    MismatchQuarantineSummaryMarker,
    MismatchQuarantineUserSummary,
)


class InMemoryMismatchQuarantineRepository(MismatchQuarantineRepository):
    """In-memory quarantine repository keyed by (source_type, source_ref_id)."""

    _MAX_ENTRIES = 10000

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records_by_source: dict[tuple[str, str], MismatchQuarantineRecord] = {}

    async def upsert_by_source(
        self,
        record: MismatchQuarantineRecord,
    ) -> MismatchQuarantineRecord:
        key = (record.source_type.value, record.source_ref_id)
        async with self._lock:
            self._records_by_source[key] = record
            if len(self._records_by_source) > self._MAX_ENTRIES:
                evict_keys = list(self._records_by_source.keys())[: len(self._records_by_source) // 4]
                for k in evict_keys:
                    del self._records_by_source[k]
            return record

    async def get_user_quarantine_summary(
        self,
        internal_user_id: str,
    ) -> MismatchQuarantineUserSummary:
        async with self._lock:
            active_for_user: tuple[MismatchQuarantineRecord, ...] = tuple(
                r
                for r in self._iter_records_locked()
                if r.internal_user_id == internal_user_id
                and r.resolution_status is MismatchQuarantineResolutionStatus.ACTIVE
            )

        if not active_for_user:
            return MismatchQuarantineUserSummary(
                marker=MismatchQuarantineSummaryMarker.NONE,
                reason_code=MismatchQuarantineReasonCode.NONE,
            )

        newest = max(active_for_user, key=lambda r: r.updated_at)
        return MismatchQuarantineUserSummary(
            marker=MismatchQuarantineSummaryMarker.ACTIVE,
            reason_code=newest.reason_code,
        )

    async def records_for_tests(self) -> tuple[MismatchQuarantineRecord, ...]:
        """Test-only helper to observe upsert behaviour."""
        async with self._lock:
            return tuple(self._iter_records_locked())

    def _iter_records_locked(self) -> Iterable[MismatchQuarantineRecord]:
        # Internal helper: assumes caller holds _lock.
        return tuple(self._records_by_source.values())
