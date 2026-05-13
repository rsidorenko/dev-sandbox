from __future__ import annotations

from app.admin_support.contracts import (
    Adm02ReconciliationDiagnostics,
    Adm02ReconciliationReadPort,
    Adm02ReconciliationRunMarker,
)
from app.persistence.reconciliation_runs_contracts import (
    ReconciliationRunOutcome,
    ReconciliationRunsRepository,
)


class Adm02ReconciliationRunsReadAdapter(Adm02ReconciliationReadPort):
    """Thin adapter from ReconciliationRunsRepository to Adm02ReconciliationReadPort."""

    def __init__(self, repo: ReconciliationRunsRepository) -> None:
        self._repo = repo

    async def get_reconciliation_diagnostics(self, internal_user_id: str) -> Adm02ReconciliationDiagnostics:
        summary = await self._repo.get_user_reconciliation_summary(internal_user_id)

        if summary.last_run_marker is ReconciliationRunOutcome.NO_CHANGES:
            marker = Adm02ReconciliationRunMarker.NO_CHANGES

        elif summary.last_run_marker is ReconciliationRunOutcome.FACTS_DISCOVERED:
            marker = Adm02ReconciliationRunMarker.FACTS_DISCOVERED

        elif summary.last_run_marker is ReconciliationRunOutcome.UNKNOWN:
            marker = Adm02ReconciliationRunMarker.UNKNOWN

        else:
            # Fail-closed: any non-enumerated outcome is treated as UNKNOWN.

            marker = Adm02ReconciliationRunMarker.UNKNOWN

        return Adm02ReconciliationDiagnostics(last_run_marker=marker)
