from __future__ import annotations

from datetime import UTC, datetime

from app.persistence.mismatch_quarantine_contracts import (
    MismatchQuarantineReasonCode,
    MismatchQuarantineRecord,
    MismatchQuarantineRepository,
    MismatchQuarantineResolutionStatus,
    MismatchQuarantineSourceType,
    MismatchQuarantineSummaryMarker,
    MismatchQuarantineUserSummary,
)


def test_mismatch_quarantine_record_constructs() -> None:
    now = datetime.now(UTC)

    record = MismatchQuarantineRecord(
        id="q-1",
        source_type=MismatchQuarantineSourceType.RECONCILIATION_RUN,
        source_ref_id="run-1:event-1",
        internal_user_id="user-1",
        reason_code=MismatchQuarantineReasonCode.MISMATCH,
        resolution_status=MismatchQuarantineResolutionStatus.ACTIVE,
        reconciliation_run_id="run-1",
        created_at=now,
        updated_at=now,
        resolved_at=None,
        resolved_by_admin_id=None,
    )

    assert record.id == "q-1"
    assert record.source_type is MismatchQuarantineSourceType.RECONCILIATION_RUN
    assert record.reason_code is MismatchQuarantineReasonCode.MISMATCH


def test_mismatch_quarantine_user_summary_constructs() -> None:
    summary = MismatchQuarantineUserSummary(
        marker=MismatchQuarantineSummaryMarker.ACTIVE,
        reason_code=MismatchQuarantineReasonCode.MISMATCH,
    )

    assert summary.marker is MismatchQuarantineSummaryMarker.ACTIVE
    assert summary.reason_code is MismatchQuarantineReasonCode.MISMATCH


def test_mismatch_quarantine_repository_protocol_shape() -> None:
    protocol_dict = MismatchQuarantineRepository.__dict__
    method_names = {name for name, value in protocol_dict.items() if callable(value) and not name.startswith("_")}

    assert method_names == {
        "upsert_by_source",
        "get_user_quarantine_summary",
    }
