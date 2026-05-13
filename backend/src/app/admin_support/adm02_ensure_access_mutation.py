"""ADM-02 ensure-access mutation adapters and opt-in gate helpers."""

from __future__ import annotations

import hashlib

from app.admin_support.contracts import Adm02MutationOptInPort
from app.issuance.contracts import IssuanceOperationType, IssuanceOutcomeCategory, IssuanceRequest
from app.issuance.service import IssuanceService
from app.shared.types import SubscriptionSnapshotState


class FixedAdm02MutationOptIn(Adm02MutationOptInPort):
    """Static mutation opt-in gate used by composition/tests."""

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    async def check_adm02_mutation_opt_in_enabled(self, *, correlation_id: str) -> bool:
        del correlation_id
        return self._enabled


class Adm02EnsureAccessIssuanceMutationAdapter:
    """Issue access idempotently through existing IssuanceService path."""

    def __init__(self, issuance_service: IssuanceService) -> None:
        self._service = issuance_service

    async def ensure_access_issued(self, internal_user_id: str, *, correlation_id: str) -> bool:
        idempotency_key = _deterministic_ensure_access_idempotency_key(internal_user_id)
        req = IssuanceRequest(
            internal_user_id=internal_user_id,
            subscription_state=SubscriptionSnapshotState.ACTIVE,
            operation=IssuanceOperationType.ISSUE,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            link_issue_idempotency_key=None,
        )
        result = await self._service.execute(req)
        if result.category is IssuanceOutcomeCategory.ISSUED:
            return True
        if result.category is IssuanceOutcomeCategory.ALREADY_ISSUED:
            return False
        raise RuntimeError("ensure access issuance failed safely")


def _deterministic_ensure_access_idempotency_key(internal_user_id: str) -> str:
    digest = hashlib.sha256(f"adm02-ensure-access:{internal_user_id}".encode()).hexdigest()
    return f"adm02-ensure-access:{digest[:32]}"
