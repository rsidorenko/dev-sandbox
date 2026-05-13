from __future__ import annotations

from dataclasses import is_dataclass
from datetime import UTC, datetime

from app.persistence.reconciliation_runs_contracts import (
    ReconciliationRunOutcome,
    ReconciliationRunRecord,
    ReconciliationRunsRepository,
    ReconciliationRunStatus,
    ReconciliationRunUserSummary,
)


def test_reconciliation_run_record_constructs() -> None:
    started = datetime.now(UTC)

    record = ReconciliationRunRecord(
        id="run-1",
        internal_user_id="user-1",
        billing_provider_key="provider_a",
        started_at=started,
        finished_at=None,
        status=ReconciliationRunStatus.STARTED,
        outcome=ReconciliationRunOutcome.UNKNOWN,
        created_billing_fact_refs=("be-1",),
        correlation_id="corr-1",
    )

    assert is_dataclass(record)
    assert record.id == "run-1"
    assert record.status is ReconciliationRunStatus.STARTED
    assert record.created_billing_fact_refs == ("be-1",)


def test_reconciliation_run_user_summary_constructs() -> None:
    summary = ReconciliationRunUserSummary(
        last_run_marker=ReconciliationRunOutcome.FACTS_DISCOVERED,
    )

    assert is_dataclass(summary)
    assert summary.last_run_marker is ReconciliationRunOutcome.FACTS_DISCOVERED


def test_reconciliation_runs_repository_protocol_shape() -> None:
    protocol_dict = ReconciliationRunsRepository.__dict__
    method_names = {name for name, value in protocol_dict.items() if callable(value) and not name.startswith("_")}

    assert method_names == {
        "append_run_record",
        "get_user_reconciliation_summary",
    }
