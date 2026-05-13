"""Read-only ADM-02 ensure-access audit evidence lookup orchestration."""

from __future__ import annotations

from app.admin_support.contracts import (
    Adm02EnsureAccessAuditLookupInput,
    Adm02EnsureAccessAuditLookupOutcome,
    Adm02EnsureAccessAuditLookupResponse,
    Adm02EnsureAccessAuditReadPort,
    Adm02EnsureAccessAuditReadQuery,
    Adm02EnsureAccessAuthorizationPort,
)
from app.shared.correlation import is_valid_correlation_id, require_correlation_id

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


class Adm02EnsureAccessAuditLookupHandler:
    """Authorize + validate + read bounded redacted durable ADM-02 audit evidence."""

    def __init__(
        self,
        *,
        authorization: Adm02EnsureAccessAuthorizationPort,
        audit_read: Adm02EnsureAccessAuditReadPort,
    ) -> None:
        self._authorization = authorization
        self._audit_read = audit_read

    async def handle(
        self,
        inp: Adm02EnsureAccessAuditLookupInput,
    ) -> Adm02EnsureAccessAuditLookupResponse:
        cid = inp.correlation_id
        try:
            require_correlation_id(cid)
        except ValueError:
            return Adm02EnsureAccessAuditLookupResponse(
                outcome=Adm02EnsureAccessAuditLookupOutcome.INVALID_INPUT,
                correlation_id=cid,
                result=None,
            )

        evidence_cid = inp.evidence_correlation_id
        if evidence_cid is not None and not is_valid_correlation_id(evidence_cid):
            return Adm02EnsureAccessAuditLookupResponse(
                outcome=Adm02EnsureAccessAuditLookupOutcome.INVALID_INPUT,
                correlation_id=cid,
                result=None,
            )

        if inp.limit < 1 or inp.limit > _MAX_LIMIT:
            return Adm02EnsureAccessAuditLookupResponse(
                outcome=Adm02EnsureAccessAuditLookupOutcome.INVALID_INPUT,
                correlation_id=cid,
                result=None,
            )

        try:
            allowed = await self._authorization.check_adm02_ensure_access_allowed(
                inp.actor,
                correlation_id=cid,
            )
        except Exception:
            return Adm02EnsureAccessAuditLookupResponse(
                outcome=Adm02EnsureAccessAuditLookupOutcome.DEPENDENCY_FAILURE,
                correlation_id=cid,
                result=None,
            )
        if not allowed:
            return Adm02EnsureAccessAuditLookupResponse(
                outcome=Adm02EnsureAccessAuditLookupOutcome.DENIED,
                correlation_id=cid,
                result=None,
            )

        safe_limit = inp.limit if inp.limit > 0 else _DEFAULT_LIMIT
        try:
            result = await self._audit_read.read_ensure_access_audit_evidence(
                Adm02EnsureAccessAuditReadQuery(
                    correlation_id=evidence_cid,
                    limit=safe_limit,
                )
            )
        except Exception:
            return Adm02EnsureAccessAuditLookupResponse(
                outcome=Adm02EnsureAccessAuditLookupOutcome.DEPENDENCY_FAILURE,
                correlation_id=cid,
                result=None,
            )
        return Adm02EnsureAccessAuditLookupResponse(
            outcome=Adm02EnsureAccessAuditLookupOutcome.SUCCESS,
            correlation_id=cid,
            result=result,
        )
