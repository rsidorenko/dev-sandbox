"""Deterministic in-memory fake :class:`IssuanceProviderPort` for tests (no I/O, no network)."""

from __future__ import annotations

from enum import StrEnum

from app.issuance.contracts import (
    CreateAccessOutcome,
    GetSafeInstructionOutcome,
    ProviderCreateResult,
    ProviderGetSafeResult,
    ProviderRevokeResult,
    RevokeAccessOutcome,
)


class FakeProviderMode(StrEnum):
    """Simulated provider behavior for a single process."""

    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


# Opaque, non-secret tokens (safe for tests and log assertions).
_FAKE_REF_PREFIX = "issuance-ref:fake"
_FAKE_INSTR_PREFIX = "instr-handle:safe"


class FakeIssuanceProvider:
    """
    Deterministic fake: each method increments a counter; mode drives outcomes.

    Success returns only synthetic opaque refs (no pem/tokens/urls with secrets).
    """

    def __init__(self, mode: FakeProviderMode) -> None:
        self._mode = mode
        self.create_or_ensure_calls = 0
        self.revoke_access_calls = 0
        self.get_safe_delivery_calls = 0

    async def create_or_ensure_access(
        self,
        *,
        internal_user_id: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> ProviderCreateResult:
        self.create_or_ensure_calls += 1
        m = self._mode
        if m is FakeProviderMode.SUCCESS:
            ref = f"{_FAKE_REF_PREFIX}:{internal_user_id}:{idempotency_key[:16]}"
            return ProviderCreateResult(outcome=CreateAccessOutcome.SUCCESS, issuance_ref=ref)
        if m is FakeProviderMode.UNAVAILABLE:
            return ProviderCreateResult(outcome=CreateAccessOutcome.UNAVAILABLE, issuance_ref=None)
        if m is FakeProviderMode.REJECTED:
            return ProviderCreateResult(outcome=CreateAccessOutcome.REJECTED, issuance_ref=None)
        return ProviderCreateResult(outcome=CreateAccessOutcome.UNKNOWN, issuance_ref=None)

    async def revoke_access(
        self,
        *,
        internal_user_id: str,
        issuance_ref: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> ProviderRevokeResult:
        self.revoke_access_calls += 1
        m = self._mode
        if m is FakeProviderMode.SUCCESS:
            return ProviderRevokeResult(outcome=RevokeAccessOutcome.REVOKED)
        if m is FakeProviderMode.UNAVAILABLE:
            return ProviderRevokeResult(outcome=RevokeAccessOutcome.UNAVAILABLE)
        if m is FakeProviderMode.REJECTED:
            return ProviderRevokeResult(outcome=RevokeAccessOutcome.REJECTED)
        return ProviderRevokeResult(outcome=RevokeAccessOutcome.UNKNOWN)

    async def get_safe_delivery_instructions(
        self,
        *,
        internal_user_id: str,
        issuance_ref: str,
        correlation_id: str,
    ) -> ProviderGetSafeResult:
        self.get_safe_delivery_calls += 1
        m = self._mode
        if m is FakeProviderMode.SUCCESS:
            handle = f"{_FAKE_INSTR_PREFIX}:{internal_user_id[:8]}"
            return ProviderGetSafeResult(outcome=GetSafeInstructionOutcome.READY, instruction_ref=handle)
        if m is FakeProviderMode.UNAVAILABLE:
            return ProviderGetSafeResult(outcome=GetSafeInstructionOutcome.UNAVAILABLE, instruction_ref=None)
        if m is FakeProviderMode.REJECTED:
            return ProviderGetSafeResult(outcome=GetSafeInstructionOutcome.REJECTED, instruction_ref=None)
        return ProviderGetSafeResult(outcome=GetSafeInstructionOutcome.UNKNOWN, instruction_ref=None)
