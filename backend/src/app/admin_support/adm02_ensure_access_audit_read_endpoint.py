"""Framework-neutral adapter for internal ADM-02 ensure-access audit evidence lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.admin_support.contracts import (
    Adm02EnsureAccessAuditLookupInput,
    Adm02EnsureAccessAuditLookupOutcome,
    Adm02EnsureAccessAuditLookupResponse,
    InternalAdminPrincipalExtractionInput,
    InternalAdminPrincipalExtractionOutcome,
    InternalAdminPrincipalExtractor,
)

DEFAULT_AUDIT_EVIDENCE_LIMIT = 20


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessAuditLookupInboundRequest:
    correlation_id: str
    internal_admin_principal_id: str
    evidence_correlation_id: str | None = None
    limit: int = DEFAULT_AUDIT_EVIDENCE_LIMIT


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessAuditLookupEvidenceItem:
    created_at: str
    event_type: str
    outcome_bucket: str
    remediation_result: str | None
    readiness_bucket: str | None
    principal_marker: str
    correlation_id: str
    source_marker: str | None


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessAuditLookupEndpointResponse:
    outcome: str
    correlation_id: str
    items: tuple[Adm02EnsureAccessAuditLookupEvidenceItem, ...]


class Adm02EnsureAccessAuditLookupHandlerLike(Protocol):
    async def handle(self, inp: Adm02EnsureAccessAuditLookupInput) -> Adm02EnsureAccessAuditLookupResponse: ...


def _to_response(
    result: Adm02EnsureAccessAuditLookupResponse,
) -> Adm02EnsureAccessAuditLookupEndpointResponse:
    if result.outcome is not Adm02EnsureAccessAuditLookupOutcome.SUCCESS or result.result is None:
        return Adm02EnsureAccessAuditLookupEndpointResponse(
            outcome=result.outcome.value,
            correlation_id=result.correlation_id,
            items=(),
        )
    items = tuple(
        Adm02EnsureAccessAuditLookupEvidenceItem(
            created_at=item.created_at,
            event_type=item.event_type.value,
            outcome_bucket=item.outcome_bucket.value,
            remediation_result=item.remediation_result.value if item.remediation_result else None,
            readiness_bucket=item.readiness_bucket.value if item.readiness_bucket else None,
            principal_marker=item.principal_marker.value,
            correlation_id=item.correlation_id,
            source_marker=item.source_marker,
        )
        for item in result.result.items
    )
    return Adm02EnsureAccessAuditLookupEndpointResponse(
        outcome=result.outcome.value,
        correlation_id=result.correlation_id,
        items=items,
    )


async def execute_adm02_ensure_access_audit_lookup_endpoint(
    handler: Adm02EnsureAccessAuditLookupHandlerLike,
    principal_extractor: InternalAdminPrincipalExtractor,
    request: Adm02EnsureAccessAuditLookupInboundRequest,
) -> Adm02EnsureAccessAuditLookupEndpointResponse:
    principal_id_candidate = (
        request.internal_admin_principal_id if isinstance(request.internal_admin_principal_id, str) else None
    )
    try:
        extraction = await principal_extractor.extract_trusted_internal_admin_principal(
            InternalAdminPrincipalExtractionInput(
                principal_id_candidate=principal_id_candidate,
                trusted_source=True,
            )
        )
    except Exception:
        return Adm02EnsureAccessAuditLookupEndpointResponse(
            outcome=Adm02EnsureAccessAuditLookupOutcome.DEPENDENCY_FAILURE.value,
            correlation_id=request.correlation_id,
            items=(),
        )
    if extraction.outcome is not InternalAdminPrincipalExtractionOutcome.SUCCESS or extraction.principal is None:
        return Adm02EnsureAccessAuditLookupEndpointResponse(
            outcome=Adm02EnsureAccessAuditLookupOutcome.INVALID_INPUT.value,
            correlation_id=request.correlation_id,
            items=(),
        )
    try:
        result = await handler.handle(
            Adm02EnsureAccessAuditLookupInput(
                actor=extraction.principal,
                correlation_id=request.correlation_id,
                evidence_correlation_id=request.evidence_correlation_id,
                limit=request.limit,
            )
        )
    except Exception:
        return Adm02EnsureAccessAuditLookupEndpointResponse(
            outcome=Adm02EnsureAccessAuditLookupOutcome.DEPENDENCY_FAILURE.value,
            correlation_id=request.correlation_id,
            items=(),
        )
    return _to_response(result)
