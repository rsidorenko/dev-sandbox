"""ADM-02 diagnostics orchestration (read-only; no transport, storage, or observability)."""

from __future__ import annotations

from app.admin_support.contracts import (
    Adm01IdentityResolvePort,
    Adm02AuthorizationPort,
    Adm02BillingFactsReadPort,
    Adm02DiagnosticsInput,
    Adm02DiagnosticsOutcome,
    Adm02DiagnosticsResult,
    Adm02DiagnosticsSummary,
    Adm02FactOfAccessAuditPort,
    Adm02FactOfAccessAuditRecord,
    Adm02FactOfAccessDisclosureCategory,
    Adm02QuarantineReadPort,
    Adm02ReconciliationReadPort,
    Adm02RedactionPort,
    RedactionMarker,
)
from app.shared.correlation import require_correlation_id

ADM02_CAPABILITY_CLASS = "adm02_billing_quarantine_reconciliation_diagnostics"


def _disclosure_from_redaction(marker: RedactionMarker) -> Adm02FactOfAccessDisclosureCategory:
    if marker is RedactionMarker.NONE:
        return Adm02FactOfAccessDisclosureCategory.UNREDACTED

    if marker is RedactionMarker.PARTIAL:
        return Adm02FactOfAccessDisclosureCategory.PARTIAL

    return Adm02FactOfAccessDisclosureCategory.FULLY_REDACTED


class Adm02DiagnosticsHandler:
    """Validate correlation → authorize ADM-02 → resolve identity → read diagnostics → redact → fact-of-access audit."""

    def __init__(
        self,
        authorization: Adm02AuthorizationPort,
        identity: Adm01IdentityResolvePort,
        billing: Adm02BillingFactsReadPort,
        quarantine: Adm02QuarantineReadPort,
        reconciliation: Adm02ReconciliationReadPort,
        audit: Adm02FactOfAccessAuditPort,
        redaction: Adm02RedactionPort | None = None,
    ) -> None:
        self._authorization = authorization

        self._identity = identity

        self._billing = billing

        self._quarantine = quarantine

        self._reconciliation = reconciliation

        self._redaction = redaction

        self._audit = audit

    async def handle(self, inp: Adm02DiagnosticsInput) -> Adm02DiagnosticsResult:
        cid = inp.correlation_id

        try:
            require_correlation_id(cid)

        except ValueError:
            return Adm02DiagnosticsResult(
                outcome=Adm02DiagnosticsOutcome.INVALID_INPUT,
                correlation_id=cid,
                summary=None,
            )

        try:
            allowed = await self._authorization.check_adm02_diagnostics_allowed(
                inp.actor,
                correlation_id=cid,
            )

        except Exception:
            return Adm02DiagnosticsResult(
                outcome=Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE,
                correlation_id=cid,
                summary=None,
            )

        if not allowed:
            return Adm02DiagnosticsResult(
                outcome=Adm02DiagnosticsOutcome.DENIED,
                correlation_id=cid,
                summary=None,
            )

        try:
            internal_user_id = await self._identity.resolve_internal_user_id(
                inp.target,
                correlation_id=cid,
            )

        except Exception:
            return Adm02DiagnosticsResult(
                outcome=Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE,
                correlation_id=cid,
                summary=None,
            )

        if internal_user_id is None:
            return Adm02DiagnosticsResult(
                outcome=Adm02DiagnosticsOutcome.TARGET_NOT_RESOLVED,
                correlation_id=cid,
                summary=None,
            )

        try:
            billing = await self._billing.get_billing_facts_diagnostics(internal_user_id)

            quarantine = await self._quarantine.get_quarantine_diagnostics(internal_user_id)

            reconciliation = await self._reconciliation.get_reconciliation_diagnostics(internal_user_id)

        except Exception:
            return Adm02DiagnosticsResult(
                outcome=Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE,
                correlation_id=cid,
                summary=None,
            )

        summary = Adm02DiagnosticsSummary(
            billing=billing,
            quarantine=quarantine,
            reconciliation=reconciliation,
            redaction=RedactionMarker.NONE,
        )

        if self._redaction is not None:
            try:
                summary = await self._redaction.redact_diagnostics_summary(summary)

            except Exception:
                return Adm02DiagnosticsResult(
                    outcome=Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE,
                    correlation_id=cid,
                    summary=None,
                )

        try:
            await self._audit.append_fact_of_access(
                Adm02FactOfAccessAuditRecord(
                    actor=inp.actor,
                    capability_class=ADM02_CAPABILITY_CLASS,
                    internal_user_scope_ref=internal_user_id,
                    correlation_id=cid,
                    disclosure=_disclosure_from_redaction(summary.redaction),
                ),
            )

        except Exception:
            return Adm02DiagnosticsResult(
                outcome=Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE,
                correlation_id=cid,
                summary=None,
            )

        return Adm02DiagnosticsResult(
            outcome=Adm02DiagnosticsOutcome.SUCCESS,
            correlation_id=cid,
            summary=summary,
        )
