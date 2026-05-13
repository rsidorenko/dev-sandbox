from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.persistence import (
    InMemoryReconciliationRunsRepository,
    ReconciliationRunOutcome,
    ReconciliationRunRecord,
    ReconciliationRunStatus,
    ReconciliationRunUserSummary,
)


def _make_record(
    *,
    run_id: str,
    internal_user_id: str | None,
    started_at: datetime,
    outcome: ReconciliationRunOutcome = ReconciliationRunOutcome.NO_CHANGES,
    billing_provider_key: str = "provider_a",
    status: ReconciliationRunStatus = ReconciliationRunStatus.COMPLETED,
) -> ReconciliationRunRecord:
    return ReconciliationRunRecord(
        id=run_id,
        internal_user_id=internal_user_id,
        billing_provider_key=billing_provider_key,
        started_at=started_at,
        finished_at=None,
        status=status,
        outcome=outcome,
        created_billing_fact_refs=(),
        correlation_id=f"corr-{run_id}",
    )


@pytest.mark.asyncio
async def test_append_new_record_returns_same_object() -> None:
    repo = InMemoryReconciliationRunsRepository()
    record = _make_record(
        run_id="r1",
        internal_user_id="user-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    stored = await repo.append_run_record(record)

    assert stored is record
    all_records = await repo.records_for_tests()
    assert len(all_records) == 1
    assert all_records[0] is record


@pytest.mark.asyncio
async def test_append_is_append_only_order() -> None:
    repo = InMemoryReconciliationRunsRepository()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    first = _make_record(run_id="r1", internal_user_id="user-1", started_at=t0)
    second = _make_record(
        run_id="r2",
        internal_user_id="user-1",
        started_at=t0 + timedelta(seconds=1),
    )

    await repo.append_run_record(first)
    await repo.append_run_record(second)

    all_records = await repo.records_for_tests()
    assert len(all_records) == 2
    assert all_records[0] is first
    assert all_records[1] is second


@pytest.mark.asyncio
async def test_summary_for_missing_user_is_unknown() -> None:
    repo = InMemoryReconciliationRunsRepository()

    summary = await repo.get_user_reconciliation_summary("missing-user")

    assert isinstance(summary, ReconciliationRunUserSummary)
    assert summary.last_run_marker is ReconciliationRunOutcome.UNKNOWN


@pytest.mark.asyncio
async def test_summary_uses_newest_started_at_outcome() -> None:
    repo = InMemoryReconciliationRunsRepository()
    user_id = "user-1"
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    older = _make_record(
        run_id="older",
        internal_user_id=user_id,
        started_at=t0,
        outcome=ReconciliationRunOutcome.NO_CHANGES,
    )
    newer = _make_record(
        run_id="newer",
        internal_user_id=user_id,
        started_at=t0 + timedelta(hours=1),
        outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
    )

    await repo.append_run_record(newer)
    await repo.append_run_record(older)

    summary = await repo.get_user_reconciliation_summary(user_id)

    assert summary.last_run_marker is ReconciliationRunOutcome.FACTS_DISCOVERED


@pytest.mark.asyncio
async def test_summary_ignores_other_users() -> None:
    repo = InMemoryReconciliationRunsRepository()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    for_user_a = _make_record(
        run_id="a1",
        internal_user_id="user-a",
        started_at=t0,
        outcome=ReconciliationRunOutcome.NO_CHANGES,
    )
    for_user_b = _make_record(
        run_id="b1",
        internal_user_id="user-b",
        started_at=t0 + timedelta(days=99),
        outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
    )

    await repo.append_run_record(for_user_a)
    await repo.append_run_record(for_user_b)

    summary = await repo.get_user_reconciliation_summary("user-a")

    assert summary.last_run_marker is ReconciliationRunOutcome.NO_CHANGES
