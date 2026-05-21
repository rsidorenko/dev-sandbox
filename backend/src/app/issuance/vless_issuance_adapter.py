"""Adapts VlessProviderPort to IssuanceProviderPort for IssuanceService."""

from __future__ import annotations

from app.issuance.contracts import (
    CreateAccessOutcome,
    GetSafeInstructionOutcome,
    ProviderCreateResult,
    ProviderGetSafeResult,
    ProviderRevokeResult,
    RevokeAccessOutcome,
)
from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderPort


class VlessIssuanceAdapter:
    """Wraps a VlessProviderPort to satisfy the IssuanceProviderPort protocol."""

    def __init__(self, provider: VlessProviderPort) -> None:
        self._provider = provider

    async def create_or_ensure_access(
        self,
        *,
        internal_user_id: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> ProviderCreateResult:
        result = await self._provider.create_user(internal_user_id=internal_user_id)
        if result.outcome == VlessProviderOutcome.SUCCESS:
            ref = result.config.user_uuid if result.config else internal_user_id
            return ProviderCreateResult(outcome=CreateAccessOutcome.SUCCESS, issuance_ref=ref)
        if result.outcome == VlessProviderOutcome.UNAVAILABLE:
            return ProviderCreateResult(outcome=CreateAccessOutcome.UNAVAILABLE, issuance_ref=None)
        return ProviderCreateResult(outcome=CreateAccessOutcome.UNKNOWN, issuance_ref=None)

    async def revoke_access(
        self,
        *,
        internal_user_id: str,
        issuance_ref: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> ProviderRevokeResult:
        result = await self._provider.revoke_user(internal_user_id=internal_user_id)
        if result.outcome in (VlessProviderOutcome.SUCCESS, VlessProviderOutcome.NOT_FOUND):
            return ProviderRevokeResult(outcome=RevokeAccessOutcome.REVOKED)
        if result.outcome == VlessProviderOutcome.UNAVAILABLE:
            return ProviderRevokeResult(outcome=RevokeAccessOutcome.UNAVAILABLE)
        return ProviderRevokeResult(outcome=RevokeAccessOutcome.UNKNOWN)

    async def get_safe_delivery_instructions(
        self,
        *,
        internal_user_id: str,
        issuance_ref: str,
        correlation_id: str,
    ) -> ProviderGetSafeResult:
        result = await self._provider.get_user_config(internal_user_id=internal_user_id)
        if result.outcome == VlessProviderOutcome.SUCCESS and result.config:
            return ProviderGetSafeResult(
                outcome=GetSafeInstructionOutcome.READY,
                instruction_ref=result.config.subscription_url,
            )
        if result.outcome == VlessProviderOutcome.NOT_FOUND:
            return ProviderGetSafeResult(outcome=GetSafeInstructionOutcome.REJECTED, instruction_ref=None)
        return ProviderGetSafeResult(outcome=GetSafeInstructionOutcome.UNAVAILABLE, instruction_ref=None)
