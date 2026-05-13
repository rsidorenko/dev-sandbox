from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.persistence import (
    InMemoryMismatchQuarantineRepository,
    MismatchQuarantineReasonCode,
    MismatchQuarantineRecord,
    MismatchQuarantineResolutionStatus,
    MismatchQuarantineSourceType,
    MismatchQuarantineSummaryMarker,
    MismatchQuarantineUserSummary,
)


def _make_record(
    *,
    record_id: str,
    source_type: MismatchQuarantineSourceType = MismatchQuarantineSourceType.RECONCILIATION_RUN,
    source_ref_id: str = "src-1",
    internal_user_id: str | None = "user-1",
    reason_code: MismatchQuarantineReasonCode = MismatchQuarantineReasonCode.MISMATCH,
    resolution_status: MismatchQuarantineResolutionStatus = MismatchQuarantineResolutionStatus.ACTIVE,
    updated_at: datetime | None = None,
) -> MismatchQuarantineRecord:
    now = updated_at or datetime.now(UTC)
    return MismatchQuarantineRecord(
        id=record_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        internal_user_id=internal_user_id,
        reason_code=reason_code,
        resolution_status=resolution_status,
        reconciliation_run_id="run-1",
        created_at=now,
        updated_at=now,
        resolved_at=None,
        resolved_by_admin_id=None,
    )


@pytest.mark.asyncio
async def test_new_record_is_saved_and_returned() -> None:
    repo = InMemoryMismatchQuarantineRepository()
    record = _make_record(record_id="q-1", source_ref_id="src-1")

    stored = await repo.upsert_by_source(record)

    assert stored is record
    records = await repo.records_for_tests()
    assert len(records) == 1
    assert records[0] is record


@pytest.mark.asyncio
async def test_upsert_by_source_replaces_existing_record_without_duplicates() -> None:
    repo = InMemoryMismatchQuarantineRepository()

    first = _make_record(record_id="q-1", source_ref_id="dup-src")
    second = _make_record(record_id="q-2", source_ref_id="dup-src")

    stored_first = await repo.upsert_by_source(first)
    stored_second = await repo.upsert_by_source(second)

    assert stored_first is first
    assert stored_second is second

    records = await repo.records_for_tests()
    assert len(records) == 1
    assert records[0] is second


@pytest.mark.asyncio
async def test_summary_for_user_without_records_is_none_none() -> None:
    repo = InMemoryMismatchQuarantineRepository()

    summary = await repo.get_user_quarantine_summary("missing-user")

    assert isinstance(summary, MismatchQuarantineUserSummary)
    assert summary.marker is MismatchQuarantineSummaryMarker.NONE
    assert summary.reason_code is MismatchQuarantineReasonCode.NONE


@pytest.mark.asyncio
async def test_summary_for_user_with_active_record_is_active_with_reason() -> None:
    repo = InMemoryMismatchQuarantineRepository()
    user_id = "user-1"

    await repo.upsert_by_source(
        _make_record(
            record_id="q-1",
            internal_user_id=user_id,
            source_ref_id="src-1",
            reason_code=MismatchQuarantineReasonCode.MISMATCH,
            resolution_status=MismatchQuarantineResolutionStatus.ACTIVE,
        )
    )

    summary = await repo.get_user_quarantine_summary(user_id)

    assert summary.marker is MismatchQuarantineSummaryMarker.ACTIVE
    assert summary.reason_code is MismatchQuarantineReasonCode.MISMATCH


@pytest.mark.asyncio
async def test_summary_ignores_records_of_other_users() -> None:
    repo = InMemoryMismatchQuarantineRepository()

    await repo.upsert_by_source(
        _make_record(
            record_id="q-1",
            internal_user_id="other-user",
            source_ref_id="src-1",
            resolution_status=MismatchQuarantineResolutionStatus.ACTIVE,
        )
    )

    summary = await repo.get_user_quarantine_summary("target-user")

    assert summary.marker is MismatchQuarantineSummaryMarker.NONE
    assert summary.reason_code is MismatchQuarantineReasonCode.NONE


@pytest.mark.asyncio
async def test_summary_does_not_treat_resolved_as_active() -> None:
    repo = InMemoryMismatchQuarantineRepository()
    user_id = "user-1"

    await repo.upsert_by_source(
        _make_record(
            record_id="q-1",
            internal_user_id=user_id,
            source_ref_id="src-1",
            resolution_status=MismatchQuarantineResolutionStatus.RESOLVED,
        )
    )

    summary = await repo.get_user_quarantine_summary(user_id)

    assert summary.marker is MismatchQuarantineSummaryMarker.NONE
    assert summary.reason_code is MismatchQuarantineReasonCode.NONE


@pytest.mark.asyncio
async def test_summary_uses_reason_code_of_newest_active_record() -> None:
    repo = InMemoryMismatchQuarantineRepository()
    user_id = "user-1"
    older = datetime(2024, 1, 1, tzinfo=UTC)
    newer = datetime(2024, 1, 2, tzinfo=UTC)

    await repo.upsert_by_source(
        _make_record(
            record_id="q-old",
            internal_user_id=user_id,
            source_ref_id="src-1",
            reason_code=MismatchQuarantineReasonCode.MISMATCH,
            resolution_status=MismatchQuarantineResolutionStatus.ACTIVE,
            updated_at=older,
        )
    )
    await repo.upsert_by_source(
        _make_record(
            record_id="q-new",
            internal_user_id=user_id,
            source_ref_id="src-2",
            reason_code=MismatchQuarantineReasonCode.NEEDS_REVIEW,
            resolution_status=MismatchQuarantineResolutionStatus.ACTIVE,
            updated_at=newer,
        )
    )

    summary = await repo.get_user_quarantine_summary(user_id)

    assert summary.marker is MismatchQuarantineSummaryMarker.ACTIVE
    assert summary.reason_code is MismatchQuarantineReasonCode.NEEDS_REVIEW
