"""ADM-01 internal HTTP: real Starlette app + real extractor, allowlist, handler (no network/DB)."""

from __future__ import annotations

import httpx

from app.admin_support.adm01_internal_http import (
    ADM01_INTERNAL_LOOKUP_PATH,
    create_adm01_internal_http_app,
)
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


async def _post_json(app, payload: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(ADM01_INTERNAL_LOOKUP_PATH, json=payload)


def test_internal_http_composition_happy_path_real_chain() -> None:
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
    app = create_adm01_internal_http_app(
        handler,
        DefaultInternalAdminPrincipalExtractor(),
    )

    async def main() -> None:
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "  adm-allowed  ",
                "internal_user_id": "u-input",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "success"
        assert body["correlation_id"] == cid
        assert body["summary"] is not None
        s = body["summary"]
        assert s["telegram_identity_known"] is True
        assert s["subscription_bucket"] == "active"
        assert s["access_readiness_bucket"] == "active_access_ready"
        assert s["recommended_next_action"] == "ask_user_to_use_get_access"
        assert s["redaction"] == "none"
        assert identity.calls == 1
        assert identity.last_target == InternalUserTarget(internal_user_id="u-input")
        assert subscription.calls == 1
        assert entitlement.calls == 1
        assert issuance.calls == 1
        assert policy.calls == 1

    _run(main())


def test_internal_http_composition_deny_short_circuits_before_reads() -> None:
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
    app = create_adm01_internal_http_app(
        handler,
        DefaultInternalAdminPrincipalExtractor(),
    )

    async def main() -> None:
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-not-allowlisted",
                "internal_user_id": "u-input",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "denied"
        assert body["correlation_id"] == cid
        assert body["summary"] is None
        assert identity.calls == 0
        assert subscription.calls == 0
        assert entitlement.calls == 0
        assert issuance.calls == 0
        assert policy.calls == 0

    _run(main())
