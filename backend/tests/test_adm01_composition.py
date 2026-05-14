"""ADM-01 composition regression test: real extractor + allowlist + handler + endpoint."""

from __future__ import annotations

from app.admin_support.adm01_endpoint import Adm01InboundRequest, execute_adm01_endpoint
from app.admin_support.adm01_lookup import Adm01LookupHandler
from app.admin_support.authorization import AllowlistAdm01Authorization
from app.admin_support.contracts import (
    AdminPolicyFlag,
    EntitlementSummary,
    EntitlementSummaryCategory,
    InternalUserTarget,
    IssuanceOperationalState,
    IssuanceOperationalSummary,
)
from app.admin_support.principal_extraction import DefaultInternalAdminPrincipalExtractor
from app.application.interfaces import SubscriptionSnapshot
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


class _IdentityResolveFake:
    def __init__(self, resolved_internal_user_id: str | None) -> None:
        self._resolved_internal_user_id = resolved_internal_user_id
        self.calls = 0
        self.last_target: object | None = None

    async def resolve_internal_user_id(self, target, *, correlation_id: str) -> str | None:
        self.calls += 1
        self.last_target = target
        return self._resolved_internal_user_id


class _SubscriptionReadFake:
    def __init__(self, state_label: str) -> None:
        self._state_label = state_label
        self.calls = 0

    async def get_subscription_snapshot(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        self.calls += 1
        return SubscriptionSnapshot(internal_user_id=internal_user_id, state_label=self._state_label)


class _EntitlementReadFake:
    def __init__(self, category: EntitlementSummaryCategory) -> None:
        self._category = category
        self.calls = 0

    async def get_entitlement_summary(self, internal_user_id: str) -> EntitlementSummary:
        self.calls += 1
        return EntitlementSummary(category=self._category)


class _IssuanceReadFake:
    def __init__(self, state: IssuanceOperationalState) -> None:
        self._state = state
        self.calls = 0

    async def get_issuance_summary(self, internal_user_id: str) -> IssuanceOperationalSummary:
        self.calls += 1
        return IssuanceOperationalSummary(state=self._state)


class _PolicyReadFake:
    def __init__(self, flag: AdminPolicyFlag) -> None:
        self._flag = flag
        self.calls = 0

    async def get_policy_flag(self, internal_user_id: str) -> AdminPolicyFlag:
        self.calls += 1
        return self._flag


def test_adm01_composition_happy_path_real_chain() -> None:
    cid = new_correlation_id()
    identity = _IdentityResolveFake(resolved_internal_user_id="u-777")
    subscription = _SubscriptionReadFake(state_label="active")
    entitlement = _EntitlementReadFake(category=EntitlementSummaryCategory.ACTIVE)
    issuance = _IssuanceReadFake(state=IssuanceOperationalState.OK)
    policy = _PolicyReadFake(flag=AdminPolicyFlag.DEFAULT)
    handler = Adm01LookupHandler(
        authorization=AllowlistAdm01Authorization(["adm-allowed"]),
        identity=identity,
        subscription=subscription,
        entitlement=entitlement,
        issuance=issuance,
        policy=policy,
        redaction=None,
    )

    async def main() -> None:
        response = await execute_adm01_endpoint(
            handler=handler,
            principal_extractor=DefaultInternalAdminPrincipalExtractor(),
            request=Adm01InboundRequest(
                correlation_id=cid,
                internal_admin_principal_id="  adm-allowed  ",
                internal_user_id="u-input",
                telegram_user_id=None,
            ),
        )
        assert response.outcome == "success"
        assert response.summary is not None
        assert response.summary.telegram_identity_known is True
        assert response.summary.subscription_bucket == "active"
        assert response.summary.access_readiness_bucket == "active_access_ready"
        assert response.summary.recommended_next_action == "ask_user_to_use_get_access"
        assert response.summary.redaction == "none"
        assert identity.calls == 1
        assert identity.last_target == InternalUserTarget(internal_user_id="u-input")
        assert subscription.calls == 1
        assert entitlement.calls == 1
        assert issuance.calls == 1
        assert policy.calls == 1

    _run(main())


def test_adm01_composition_deny_short_circuits_before_reads() -> None:
    cid = new_correlation_id()
    identity = _IdentityResolveFake(resolved_internal_user_id="u-should-not-be-used")
    subscription = _SubscriptionReadFake(state_label="active")
    entitlement = _EntitlementReadFake(category=EntitlementSummaryCategory.ACTIVE)
    issuance = _IssuanceReadFake(state=IssuanceOperationalState.OK)
    policy = _PolicyReadFake(flag=AdminPolicyFlag.DEFAULT)
    handler = Adm01LookupHandler(
        authorization=AllowlistAdm01Authorization(["adm-only-this-one"]),
        identity=identity,
        subscription=subscription,
        entitlement=entitlement,
        issuance=issuance,
        policy=policy,
        redaction=None,
    )

    async def main() -> None:
        response = await execute_adm01_endpoint(
            handler=handler,
            principal_extractor=DefaultInternalAdminPrincipalExtractor(),
            request=Adm01InboundRequest(
                correlation_id=cid,
                internal_admin_principal_id="adm-not-allowlisted",
                telegram_user_id=424242,
                internal_user_id=None,
            ),
        )
        assert response.outcome == "denied"
        assert response.summary is None
        assert identity.calls == 0
        assert subscription.calls == 0
        assert entitlement.calls == 0
        assert issuance.calls == 0
        assert policy.calls == 0

    _run(main())
