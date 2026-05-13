from __future__ import annotations

import pytest

from app.admin_support import (
    Adm02ReconciliationDiagnostics,
    Adm02ReconciliationRunMarker,
    Adm02ReconciliationRunsReadAdapter,
)
from app.persistence import (
    ReconciliationRunOutcome,
    ReconciliationRunsRepository,
    ReconciliationRunUserSummary,
)


class _SummaryOnlyRepo(ReconciliationRunsRepository):
    def __init__(self, summary: ReconciliationRunUserSummary) -> None:
        self._summary = summary

    async def append_run_record(self, record):  # type: ignore[override]
        raise NotImplementedError

    async def get_user_reconciliation_summary(self, internal_user_id: str) -> ReconciliationRunUserSummary:  # type: ignore[override]
        return self._summary


class _FailingRepo(ReconciliationRunsRepository):
    async def append_run_record(self, record):  # type: ignore[override]
        raise NotImplementedError

    async def get_user_reconciliation_summary(self, internal_user_id: str) -> ReconciliationRunUserSummary:  # type: ignore[override]
        raise RuntimeError("reconciliation failure")


@pytest.mark.anyio
async def test_adapter_maps_unknown_summary() -> None:
    repo = _SummaryOnlyRepo(
        ReconciliationRunUserSummary(last_run_marker=ReconciliationRunOutcome.UNKNOWN),
    )

    adapter = Adm02ReconciliationRunsReadAdapter(repo)

    diagnostics = await adapter.get_reconciliation_diagnostics("user-unknown")

    assert isinstance(diagnostics, Adm02ReconciliationDiagnostics)

    assert diagnostics.last_run_marker is Adm02ReconciliationRunMarker.UNKNOWN


@pytest.mark.anyio
async def test_adapter_maps_no_changes_summary() -> None:
    repo = _SummaryOnlyRepo(
        ReconciliationRunUserSummary(last_run_marker=ReconciliationRunOutcome.NO_CHANGES),
    )

    adapter = Adm02ReconciliationRunsReadAdapter(repo)

    diagnostics = await adapter.get_reconciliation_diagnostics("user-no-changes")

    assert diagnostics.last_run_marker is Adm02ReconciliationRunMarker.NO_CHANGES


@pytest.mark.anyio
async def test_adapter_maps_facts_discovered_summary() -> None:
    repo = _SummaryOnlyRepo(
        ReconciliationRunUserSummary(last_run_marker=ReconciliationRunOutcome.FACTS_DISCOVERED),
    )

    adapter = Adm02ReconciliationRunsReadAdapter(repo)

    diagnostics = await adapter.get_reconciliation_diagnostics("user-facts")

    assert diagnostics.last_run_marker is Adm02ReconciliationRunMarker.FACTS_DISCOVERED


@pytest.mark.anyio
async def test_adapter_does_not_swallow_repository_exceptions() -> None:
    adapter = Adm02ReconciliationRunsReadAdapter(_FailingRepo())

    with pytest.raises(RuntimeError):
        await adapter.get_reconciliation_diagnostics("user-error")
