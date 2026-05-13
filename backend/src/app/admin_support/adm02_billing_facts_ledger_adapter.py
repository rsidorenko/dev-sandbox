from __future__ import annotations

from app.admin_support.contracts import (
    Adm02BillingFactsCategory,
    Adm02BillingFactsDiagnostics,
    Adm02BillingFactsReadPort,
)
from app.persistence.billing_events_ledger_contracts import (
    BillingEventsLedgerRepository,
    BillingFactsPresenceCategory,
)


class Adm02BillingFactsLedgerReadAdapter(Adm02BillingFactsReadPort):
    """Thin adapter from BillingEventsLedgerRepository to Adm02BillingFactsReadPort."""

    def __init__(self, ledger_repository: BillingEventsLedgerRepository) -> None:
        self._ledger_repository = ledger_repository

    async def get_billing_facts_diagnostics(self, internal_user_id: str) -> Adm02BillingFactsDiagnostics:
        summary = await self._ledger_repository.get_user_billing_facts_summary(internal_user_id)

        if summary.category is BillingFactsPresenceCategory.NONE:
            category = Adm02BillingFactsCategory.NONE
        elif summary.category is BillingFactsPresenceCategory.HAS_ACCEPTED:
            category = Adm02BillingFactsCategory.HAS_ACCEPTED
        else:
            # Fail-closed: any non-enumerated category is treated as UNKNOWN.
            category = Adm02BillingFactsCategory.UNKNOWN

        return Adm02BillingFactsDiagnostics(
            category=category,
            internal_fact_refs=summary.internal_fact_refs,
        )
