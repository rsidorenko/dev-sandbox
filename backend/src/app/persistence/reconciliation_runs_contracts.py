"""Contracts for reconciliation_runs persistence slice (operational run records).

Minimal enums, records and repository Protocol only; no storage, DB/SQL or adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class ReconciliationRunStatus(StrEnum):
    """Lifecycle state of a reconciliation run from persistence point of view."""

    UNKNOWN = "unknown"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class ReconciliationRunOutcome(StrEnum):
    """Normalized outcome marker for a completed reconciliation run."""

    UNKNOWN = "unknown"
    NO_CHANGES = "no_changes"
    FACTS_DISCOVERED = "facts_discovered"


@dataclass(frozen=True, slots=True)
class ReconciliationRunRecord:
    """Append-only operational record for a single reconciliation run.

    Persistence-side evidence only: identifiers, timing, low-cardinality status/outcome,
    bounded internal refs to billing ledger facts, correlation id.

    Intentionally excludes raw payloads, free-form text and provider-specific blobs.
    """

    id: str
    internal_user_id: str | None
    billing_provider_key: str
    started_at: datetime
    finished_at: datetime | None
    status: ReconciliationRunStatus
    outcome: ReconciliationRunOutcome
    created_billing_fact_refs: tuple[str, ...]
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ReconciliationRunUserSummary:
    """Minimal per-user reconciliation diagnostics summary.

    Intended as a future backing source for Adm02ReconciliationReadPort adapters,
    without depending on admin_support types.
    """

    last_run_marker: ReconciliationRunOutcome


class ReconciliationRunsRepository(Protocol):
    """Append-only persistence contract for reconciliation_runs (first slice)."""

    async def append_run_record(
        self,
        record: ReconciliationRunRecord,
    ) -> ReconciliationRunRecord:
        """Persist a new run record and return the stored representation (append-only)."""

    async def get_user_reconciliation_summary(
        self,
        internal_user_id: str,
    ) -> ReconciliationRunUserSummary:
        """Return minimal per-user reconciliation summary."""
