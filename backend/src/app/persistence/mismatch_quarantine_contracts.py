"""Contracts for mismatch_quarantine persistence slice (ADM-02 quarantine diagnostics backing).

Minimal enums, records and repository Protocol only; no storage, DB/SQL or adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class MismatchQuarantineSourceType(StrEnum):
    """Origin of a mismatch_quarantine record (coarse-grained, low-cardinality)."""

    UNKNOWN = "unknown"
    RECONCILIATION_RUN = "reconciliation_run"


class MismatchQuarantineReasonCode(StrEnum):
    """Normalized reason for putting a record into quarantine (aligned with ADM-02 docs)."""

    UNKNOWN = "unknown"
    NONE = "none"
    MISMATCH = "mismatch"
    NEEDS_REVIEW = "needs_review"


class MismatchQuarantineResolutionStatus(StrEnum):
    """Minimal lifecycle state of a quarantine record from persistence point of view."""

    UNKNOWN = "unknown"
    ACTIVE = "active"
    RESOLVED = "resolved"


class MismatchQuarantineSummaryMarker(StrEnum):
    """Per-user aggregate marker for quarantine presence."""

    UNKNOWN = "unknown"
    NONE = "none"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class MismatchQuarantineRecord:
    """Single mismatch_quarantine record stored in persistence.

    The id field is opaque and storage-specific; adapters may map it to primary keys
    or document identifiers. No raw payloads or external customer identifiers are kept here.
    """

    id: str
    source_type: MismatchQuarantineSourceType
    source_ref_id: str
    internal_user_id: str | None
    reason_code: MismatchQuarantineReasonCode
    resolution_status: MismatchQuarantineResolutionStatus
    reconciliation_run_id: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    resolved_by_admin_id: str | None


@dataclass(frozen=True, slots=True)
class MismatchQuarantineUserSummary:
    """Minimal per-user quarantine diagnostics summary for future Adm02QuarantineReadPort adapter."""

    marker: MismatchQuarantineSummaryMarker
    reason_code: MismatchQuarantineReasonCode


class MismatchQuarantineRepository(Protocol):
    """Persistence contract for mismatch_quarantine slice.

    Implementations must use (source_type, source_ref_id) as a logical key and provide
    idempotent upsert semantics for that pair.
    """

    async def upsert_by_source(
        self,
        record: MismatchQuarantineRecord,
    ) -> MismatchQuarantineRecord:
        """Insert or update a record identified by (source_type, source_ref_id)."""

    async def get_user_quarantine_summary(
        self,
        internal_user_id: str,
    ) -> MismatchQuarantineUserSummary:
        """Return minimal per-user quarantine diagnostics summary."""
