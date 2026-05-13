"""Framework-neutral ADM-01 internal admin transport adapter (read-only; no HTTP/router)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.admin_support.contracts import (
    Adm01LookupInput,
    Adm01LookupOutcome,
    Adm01LookupResult,
    Adm01LookupSummary,
    AdminActorRef,
    InternalAdminPrincipalExtractionInput,
    InternalAdminPrincipalExtractionOutcome,
    InternalAdminPrincipalExtractor,
    InternalUserTarget,
    TelegramUserTarget,
)
from app.shared.correlation import is_valid_correlation_id


@dataclass(frozen=True, slots=True)
class Adm01InboundRequest:
    """Allowlisted ingress: explicit correlation id and exactly one lookup target."""

    correlation_id: str
    internal_admin_principal_id: str
    internal_user_id: str | None = None
    telegram_user_id: int | None = None


@dataclass(frozen=True, slots=True)
class Adm01OutboundSummary:
    """Safe projection of handler summary (primitives / string enums only)."""

    telegram_identity_known: bool
    subscription_bucket: str
    access_readiness_bucket: str
    recommended_next_action: str
    redaction: str


@dataclass(frozen=True, slots=True)
class Adm01EndpointResponse:
    outcome: str
    correlation_id: str
    summary: Adm01OutboundSummary | None


class Adm01LookupHandlerLike(Protocol):
    async def handle(self, inp: Adm01LookupInput) -> Adm01LookupResult: ...


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
    req: Adm01InboundRequest,
    actor: AdminActorRef,
) -> Adm01LookupInput | None:
    if not is_valid_correlation_id(req.correlation_id):
        return None
    target = _parse_target(req.internal_user_id, req.telegram_user_id)
    if target is None:
        return None
    return Adm01LookupInput(
        actor=actor,
        target=target,
        correlation_id=req.correlation_id,
    )


def _summary_to_outbound(summary: Adm01LookupSummary) -> Adm01OutboundSummary:
    readiness = summary.support_readiness
    return Adm01OutboundSummary(
        telegram_identity_known=readiness.telegram_identity_known,
        subscription_bucket=readiness.subscription_bucket.value,
        access_readiness_bucket=readiness.access_readiness_bucket.value,
        recommended_next_action=readiness.recommended_next_action.value,
        redaction=summary.redaction.value,
    )


def _result_to_response(result: Adm01LookupResult) -> Adm01EndpointResponse:
    summary_out: Adm01OutboundSummary | None = None
    if result.outcome is Adm01LookupOutcome.SUCCESS and result.summary is not None:
        summary_out = _summary_to_outbound(result.summary)
    return Adm01EndpointResponse(
        outcome=result.outcome.value,
        correlation_id=result.correlation_id,
        summary=summary_out,
    )


async def execute_adm01_endpoint(
    handler: Adm01LookupHandlerLike,
    principal_extractor: InternalAdminPrincipalExtractor,
    request: Adm01InboundRequest,
) -> Adm01EndpointResponse:
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
        return Adm01EndpointResponse(
            outcome=Adm01LookupOutcome.DEPENDENCY_FAILURE.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    if extraction.outcome is not InternalAdminPrincipalExtractionOutcome.SUCCESS or extraction.principal is None:
        return Adm01EndpointResponse(
            outcome=Adm01LookupOutcome.INVALID_INPUT.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    inp = _try_build_input(request, extraction.principal)
    if inp is None:
        return Adm01EndpointResponse(
            outcome=Adm01LookupOutcome.INVALID_INPUT.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    try:
        result = await handler.handle(inp)
    except Exception:
        return Adm01EndpointResponse(
            outcome=Adm01LookupOutcome.DEPENDENCY_FAILURE.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    return _result_to_response(result)
