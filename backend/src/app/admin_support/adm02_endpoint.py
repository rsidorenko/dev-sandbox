"""Framework-neutral ADM-02 internal admin transport adapter (read-only; no HTTP/router)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.admin_support.contracts import (
    Adm02DiagnosticsInput,
    Adm02DiagnosticsOutcome,
    Adm02DiagnosticsResult,
    Adm02DiagnosticsSummary,
    AdminActorRef,
    InternalAdminPrincipalExtractionInput,
    InternalAdminPrincipalExtractionOutcome,
    InternalAdminPrincipalExtractor,
    InternalUserTarget,
    TelegramUserTarget,
)
from app.shared.correlation import is_valid_correlation_id


@dataclass(frozen=True, slots=True)
class Adm02InboundRequest:
    """Allowlisted ingress: explicit correlation id and exactly one diagnostics target."""

    correlation_id: str
    internal_admin_principal_id: str
    internal_user_id: str | None = None
    telegram_user_id: int | None = None


@dataclass(frozen=True, slots=True)
class Adm02OutboundSummary:
    """Safe projection of handler summary (primitives / string enums only)."""

    billing_category: str
    internal_fact_refs: tuple[str, ...]
    quarantine_marker: str
    quarantine_reason_code: str
    reconciliation_last_run_marker: str
    redaction: str


@dataclass(frozen=True, slots=True)
class Adm02EndpointResponse:
    outcome: str
    correlation_id: str
    summary: Adm02OutboundSummary | None


class Adm02DiagnosticsHandlerLike(Protocol):
    async def handle(self, inp: Adm02DiagnosticsInput) -> Adm02DiagnosticsResult: ...


def _parse_target(
    internal_user_id: str | None,
    telegram_user_id: int | None,
) -> InternalUserTarget | TelegramUserTarget | None:
    has_internal = internal_user_id is not None
    has_tg = telegram_user_id is not None
    if has_internal and has_tg:
        return None
    if not has_internal and not has_tg:
        return None
    if has_internal:
        if not isinstance(internal_user_id, str):
            return None
        if internal_user_id != internal_user_id.strip():
            return None
        if not internal_user_id:
            return None
        return InternalUserTarget(internal_user_id=internal_user_id)
    if type(telegram_user_id) is not int:
        return None
    if telegram_user_id <= 0:
        return None
    return TelegramUserTarget(telegram_user_id=telegram_user_id)


def _try_build_input(
    req: Adm02InboundRequest,
    actor: AdminActorRef,
) -> Adm02DiagnosticsInput | None:
    if not is_valid_correlation_id(req.correlation_id):
        return None
    target = _parse_target(req.internal_user_id, req.telegram_user_id)
    if target is None:
        return None
    return Adm02DiagnosticsInput(
        actor=actor,
        target=target,
        correlation_id=req.correlation_id,
    )


def _summary_to_outbound(summary: Adm02DiagnosticsSummary) -> Adm02OutboundSummary:
    return Adm02OutboundSummary(
        billing_category=summary.billing.category.value,
        internal_fact_refs=summary.billing.internal_fact_refs,
        quarantine_marker=summary.quarantine.marker.value,
        quarantine_reason_code=summary.quarantine.reason_code.value,
        reconciliation_last_run_marker=summary.reconciliation.last_run_marker.value,
        redaction=summary.redaction.value,
    )


def _result_to_response(result: Adm02DiagnosticsResult) -> Adm02EndpointResponse:
    summary_out: Adm02OutboundSummary | None = None
    if result.outcome is Adm02DiagnosticsOutcome.SUCCESS and result.summary is not None:
        summary_out = _summary_to_outbound(result.summary)
    return Adm02EndpointResponse(
        outcome=result.outcome.value,
        correlation_id=result.correlation_id,
        summary=summary_out,
    )


async def execute_adm02_endpoint(
    handler: Adm02DiagnosticsHandlerLike,
    principal_extractor: InternalAdminPrincipalExtractor,
    request: Adm02InboundRequest,
) -> Adm02EndpointResponse:
    """Map allowlisted inbound request → handler input; return safe outbound shape."""
    principal_id_candidate = (
        request.internal_admin_principal_id if isinstance(request.internal_admin_principal_id, str) else None
    )
    try:
        extraction = await principal_extractor.extract_trusted_internal_admin_principal(
            InternalAdminPrincipalExtractionInput(
                principal_id_candidate=principal_id_candidate,
                trusted_source=True,
            ),
        )
    except Exception:
        return Adm02EndpointResponse(
            outcome=Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    if extraction.outcome is not InternalAdminPrincipalExtractionOutcome.SUCCESS or extraction.principal is None:
        return Adm02EndpointResponse(
            outcome=Adm02DiagnosticsOutcome.INVALID_INPUT.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    inp = _try_build_input(request, extraction.principal)
    if inp is None:
        return Adm02EndpointResponse(
            outcome=Adm02DiagnosticsOutcome.INVALID_INPUT.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    try:
        result = await handler.handle(inp)
    except Exception:
        return Adm02EndpointResponse(
            outcome=Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    return _result_to_response(result)
