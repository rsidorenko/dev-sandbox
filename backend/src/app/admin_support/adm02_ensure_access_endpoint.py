"""Framework-neutral ADM-02 ensure-access transport adapter (internal-only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.admin_support.contracts import (
    Adm02EnsureAccessInput,
    Adm02EnsureAccessOutcome,
    Adm02EnsureAccessResult,
    AdminActorRef,
    InternalAdminPrincipalExtractionInput,
    InternalAdminPrincipalExtractionOutcome,
    InternalAdminPrincipalExtractor,
    InternalUserTarget,
    TelegramUserTarget,
)
from app.shared.correlation import is_valid_correlation_id


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessInboundRequest:
    correlation_id: str
    internal_admin_principal_id: str
    internal_user_id: str | None = None
    telegram_user_id: int | None = None


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessOutboundSummary:
    telegram_identity_known: bool
    subscription_bucket: str
    access_readiness_bucket: str
    remediation_result: str
    recommended_next_action: str


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessEndpointResponse:
    outcome: str
    correlation_id: str
    summary: Adm02EnsureAccessOutboundSummary | None


class Adm02EnsureAccessHandlerLike(Protocol):
    async def handle(self, inp: Adm02EnsureAccessInput) -> Adm02EnsureAccessResult: ...


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
    req: Adm02EnsureAccessInboundRequest,
    actor: AdminActorRef,
) -> Adm02EnsureAccessInput | None:
    if not is_valid_correlation_id(req.correlation_id):
        return None
    target = _parse_target(req.internal_user_id, req.telegram_user_id)
    if target is None:
        return None
    return Adm02EnsureAccessInput(
        actor=actor,
        target=target,
        correlation_id=req.correlation_id,
    )


def _result_to_response(result: Adm02EnsureAccessResult) -> Adm02EnsureAccessEndpointResponse:
    summary_out: Adm02EnsureAccessOutboundSummary | None = None
    if result.outcome is Adm02EnsureAccessOutcome.SUCCESS and result.summary is not None:
        s = result.summary
        summary_out = Adm02EnsureAccessOutboundSummary(
            telegram_identity_known=s.telegram_identity_known,
            subscription_bucket=s.subscription_bucket.value,
            access_readiness_bucket=s.access_readiness_bucket.value,
            remediation_result=s.remediation_result.value,
            recommended_next_action=s.recommended_next_action.value,
        )
    return Adm02EnsureAccessEndpointResponse(
        outcome=result.outcome.value,
        correlation_id=result.correlation_id,
        summary=summary_out,
    )


async def execute_adm02_ensure_access_endpoint(
    handler: Adm02EnsureAccessHandlerLike,
    principal_extractor: InternalAdminPrincipalExtractor,
    request: Adm02EnsureAccessInboundRequest,
) -> Adm02EnsureAccessEndpointResponse:
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
        return Adm02EnsureAccessEndpointResponse(
            outcome=Adm02EnsureAccessOutcome.DEPENDENCY_FAILURE.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    if extraction.outcome is not InternalAdminPrincipalExtractionOutcome.SUCCESS or extraction.principal is None:
        return Adm02EnsureAccessEndpointResponse(
            outcome=Adm02EnsureAccessOutcome.INVALID_INPUT.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    inp = _try_build_input(request, extraction.principal)
    if inp is None:
        return Adm02EnsureAccessEndpointResponse(
            outcome=Adm02EnsureAccessOutcome.INVALID_INPUT.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    try:
        result = await handler.handle(inp)
    except Exception:
        return Adm02EnsureAccessEndpointResponse(
            outcome=Adm02EnsureAccessOutcome.DEPENDENCY_FAILURE.value,
            correlation_id=request.correlation_id,
            summary=None,
        )
    return _result_to_response(result)
