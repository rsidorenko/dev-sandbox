"""ADM-01 lookup handler unit tests (fakes only; no network/DB)."""

from __future__ import annotations

from dataclasses import replace

from app.admin_support.adm01_lookup import Adm01LookupHandler
from app.admin_support.contracts import (
    Adm01LookupInput,
    Adm01LookupOutcome,
    Adm01LookupSummary,
    Adm01SubscriptionStatusSummary,
    Adm01SupportAccessReadinessBucket,
    Adm01SupportNextAction,
    AdminActorRef,
    AdminPolicyFlag,
    EntitlementSummary,
    EntitlementSummaryCategory,
    InternalUserTarget,
    IssuanceOperationalState,
    IssuanceOperationalSummary,
    RedactionMarker,
    TelegramUserTarget,
)
from app.application.interfaces import SubscriptionSnapshot
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


class _AuthAllow:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed

    async def check_adm01_lookup_allowed(self, actor, *, correlation_id: str) -> bool:
        return self._allowed


class _AuthRaise:
    async def check_adm01_lookup_allowed(self, actor, *, correlation_id: str) -> bool:
        raise RuntimeError("auth failed")


class _Identity:
    def __init__(self, uid: str | None) -> None:
        self._uid = uid

    async def resolve_internal_user_id(self, target, *, correlation_id: str) -> str | None:
        return self._uid


class _IdentityRaise:
    async def resolve_internal_user_id(self, target, *, correlation_id: str) -> str | None:
        raise RuntimeError("identity failed")


class _Reads:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_subscription_snapshot(self, internal_user_id: str):
        self.calls.append("sub")
        return SubscriptionSnapshot(internal_user_id=internal_user_id, state_label="inactive")

    async def get_entitlement_summary(self, internal_user_id: str):
        self.calls.append("ent")
        return EntitlementSummary(category=EntitlementSummaryCategory.ACTIVE)

    async def get_issuance_summary(self, internal_user_id: str):
        self.calls.append("iss")
        return IssuanceOperationalSummary(state=IssuanceOperationalState.OK)


class _ReadsSpy(_Reads):
    def __init__(self) -> None:
        super().__init__()
        self.any_calls = 0

    async def get_subscription_snapshot(self, internal_user_id: str):
        self.any_calls += 1
        return await super().get_subscription_snapshot(internal_user_id)

    async def get_entitlement_summary(self, internal_user_id: str):
        self.any_calls += 1
        return await super().get_entitlement_summary(internal_user_id)

    async def get_issuance_summary(self, internal_user_id: str):
        self.any_calls += 1
        return await super().get_issuance_summary(internal_user_id)


class _Policy:
    def __init__(self, flag: AdminPolicyFlag = AdminPolicyFlag.DEFAULT) -> None:
        self._flag = flag
        self.calls: list[str] = []

    async def get_policy_flag(self, internal_user_id: str) -> AdminPolicyFlag:
        self.calls.append("policy")
        return self._flag


class _PolicyRaise:
    async def get_policy_flag(self, internal_user_id: str) -> AdminPolicyFlag:
        raise RuntimeError("policy failed")


class _IssuanceRaise:
    async def get_issuance_summary(self, internal_user_id: str):
        raise RuntimeError("issuance failed")


class _RedactionPartial:
    """Returns a redacted copy; asserts handler assembled summary first."""

    async def redact_lookup_summary(self, summary: Adm01LookupSummary) -> Adm01LookupSummary:
        assert summary.redaction is RedactionMarker.NONE
        return replace(
            summary,
            subscription=Adm01SubscriptionStatusSummary(snapshot=None),
            redaction=RedactionMarker.PARTIAL,
        )


class _RedactionRaise:
    async def redact_lookup_summary(self, summary: Adm01LookupSummary) -> Adm01LookupSummary:
        raise RuntimeError("redaction failed")


class _RedactionSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def redact_lookup_summary(self, summary: Adm01LookupSummary) -> Adm01LookupSummary:
        self.calls += 1
        return summary


def _inp(target, cid: str | None = None) -> Adm01LookupInput:
    return Adm01LookupInput(
        actor=AdminActorRef(internal_admin_principal_id="adm-1"),
        target=target,
        correlation_id=cid if cid is not None else new_correlation_id(),
    )


def test_adm01_lookup_success() -> None:
    async def main() -> None:
        reads = _Reads()
        policy = _Policy(AdminPolicyFlag.ENFORCE_MANUAL_REVIEW)
        h = Adm01LookupHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            subscription=reads,
            entitlement=reads,
            issuance=reads,
            policy=policy,
            redaction=None,
        )
        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))
        assert r.outcome is Adm01LookupOutcome.SUCCESS
        assert r.summary is not None
        assert r.summary.subscription.snapshot is not None
        assert r.summary.subscription.snapshot.state_label == "inactive"
        assert r.summary.entitlement.category is EntitlementSummaryCategory.ACTIVE
        assert r.summary.policy_flag is AdminPolicyFlag.ENFORCE_MANUAL_REVIEW
        assert r.summary.support_readiness.telegram_identity_known is True
        assert (
            r.summary.support_readiness.access_readiness_bucket
            is Adm01SupportAccessReadinessBucket.NOT_APPLICABLE_NO_ACTIVE_SUBSCRIPTION
        )
        assert r.summary.support_readiness.recommended_next_action is Adm01SupportNextAction.INVESTIGATE_BILLING_APPLY
        assert reads.calls == ["sub", "ent", "iss"]
        assert policy.calls == ["policy"]

    _run(main())


def test_adm01_lookup_denied() -> None:
    async def main() -> None:
        h = Adm01LookupHandler(
            authorization=_AuthAllow(False),
            identity=_Identity("u-1"),
            subscription=_Reads(),
            entitlement=_Reads(),
            issuance=_Reads(),
            policy=_Policy(),
        )
        r = await h.handle(_inp(TelegramUserTarget(telegram_user_id=42)))
        assert r.outcome is Adm01LookupOutcome.DENIED
        assert r.summary is None

    _run(main())


def test_adm01_lookup_target_not_resolved() -> None:
    async def main() -> None:
        h = Adm01LookupHandler(
            authorization=_AuthAllow(True),
            identity=_Identity(None),
            subscription=_Reads(),
            entitlement=_Reads(),
            issuance=_Reads(),
            policy=_Policy(),
        )
        r = await h.handle(_inp(TelegramUserTarget(telegram_user_id=99)))
        assert r.outcome is Adm01LookupOutcome.SUCCESS
        assert r.summary is not None
        assert r.summary.support_readiness.telegram_identity_known is False
        assert r.summary.support_readiness.recommended_next_action is Adm01SupportNextAction.ASK_USER_TO_USE_STATUS

    _run(main())


def test_adm01_lookup_invalid_correlation_id() -> None:
    async def main() -> None:
        h = Adm01LookupHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            subscription=_Reads(),
            entitlement=_Reads(),
            issuance=_Reads(),
            policy=_Policy(),
        )
        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1"), cid="not-a-valid-cid"))
        assert r.outcome is Adm01LookupOutcome.INVALID_INPUT
        assert r.summary is None

    _run(main())


def test_adm01_lookup_success_with_redaction() -> None:
    async def main() -> None:
        reads = _Reads()
        red = _RedactionPartial()
        h = Adm01LookupHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            subscription=reads,
            entitlement=reads,
            issuance=reads,
            policy=_Policy(),
            redaction=red,
        )
        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))
        assert r.outcome is Adm01LookupOutcome.SUCCESS
        assert r.summary is not None
        assert r.summary.redaction is RedactionMarker.PARTIAL
        assert r.summary.subscription.snapshot is None

    _run(main())


def test_adm01_lookup_redaction_exception_dependency_failure() -> None:
    async def main() -> None:
        h = Adm01LookupHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            subscription=_Reads(),
            entitlement=_Reads(),
            issuance=_Reads(),
            policy=_Policy(),
            redaction=_RedactionRaise(),
        )
        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))
        assert r.outcome is Adm01LookupOutcome.DEPENDENCY_FAILURE
        assert r.summary is None

    _run(main())


def test_adm01_lookup_redaction_skipped_on_denied() -> None:
    async def main() -> None:
        spy = _RedactionSpy()
        h = Adm01LookupHandler(
            authorization=_AuthAllow(False),
            identity=_Identity("u-1"),
            subscription=_Reads(),
            entitlement=_Reads(),
            issuance=_Reads(),
            policy=_Policy(),
            redaction=spy,
        )
        r = await h.handle(_inp(TelegramUserTarget(telegram_user_id=1)))
        assert r.outcome is Adm01LookupOutcome.DENIED
        assert spy.calls == 0

    _run(main())


def test_adm01_lookup_auth_exception_dependency_failure_short_circuit() -> None:
    async def main() -> None:
        identity = _Identity("u-1")
        reads = _ReadsSpy()
        redaction = _RedactionSpy()
        h = Adm01LookupHandler(
            authorization=_AuthRaise(),
            identity=identity,
            subscription=reads,
            entitlement=reads,
            issuance=reads,
            policy=_Policy(),
            redaction=redaction,
        )
        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))
        assert r.outcome is Adm01LookupOutcome.DEPENDENCY_FAILURE
        assert r.summary is None
        assert reads.any_calls == 0
        assert redaction.calls == 0

    _run(main())


def test_adm01_lookup_identity_exception_dependency_failure_short_circuit() -> None:
    async def main() -> None:
        reads = _ReadsSpy()
        policy = _Policy()
        redaction = _RedactionSpy()
        h = Adm01LookupHandler(
            authorization=_AuthAllow(True),
            identity=_IdentityRaise(),
            subscription=reads,
            entitlement=reads,
            issuance=reads,
            policy=policy,
            redaction=redaction,
        )
        r = await h.handle(_inp(TelegramUserTarget(telegram_user_id=7)))
        assert r.outcome is Adm01LookupOutcome.DEPENDENCY_FAILURE
        assert r.summary is None
        assert reads.any_calls == 0
        assert policy.calls == []
        assert redaction.calls == 0

    _run(main())


def test_adm01_lookup_policy_exception_dependency_failure_redaction_skipped() -> None:
    async def main() -> None:
        reads = _Reads()
        redaction = _RedactionSpy()
        h = Adm01LookupHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            subscription=reads,
            entitlement=reads,
            issuance=reads,
            policy=_PolicyRaise(),
            redaction=redaction,
        )
        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))
        assert r.outcome is Adm01LookupOutcome.SUCCESS
        assert r.summary is not None
        assert (
            r.summary.support_readiness.access_readiness_bucket
            is Adm01SupportAccessReadinessBucket.UNKNOWN_DUE_TO_INTERNAL_ERROR
        )
        assert r.summary.support_readiness.recommended_next_action is Adm01SupportNextAction.INVESTIGATE_ISSUANCE
        assert reads.calls == ["sub", "ent", "iss"]
        assert redaction.calls == 0

    _run(main())


def test_adm01_lookup_known_user_without_active_subscription_not_applicable() -> None:
    async def main() -> None:
        reads = _Reads()
        policy = _Policy()
        h = Adm01LookupHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            subscription=reads,
            entitlement=reads,
            issuance=reads,
            policy=policy,
            redaction=None,
        )
        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))
        assert r.outcome is Adm01LookupOutcome.SUCCESS
        assert r.summary is not None
        assert r.summary.support_readiness.telegram_identity_known is True
        assert (
            r.summary.support_readiness.access_readiness_bucket
            is Adm01SupportAccessReadinessBucket.NOT_APPLICABLE_NO_ACTIVE_SUBSCRIPTION
        )
        assert r.summary.support_readiness.recommended_next_action is Adm01SupportNextAction.INVESTIGATE_BILLING_APPLY

    _run(main())


def test_adm01_lookup_active_subscription_without_issuance_access_not_ready() -> None:
    class _ReadsActiveNoIssuance:
        async def get_subscription_snapshot(self, internal_user_id: str):
            return SubscriptionSnapshot(internal_user_id=internal_user_id, state_label="active")

        async def get_entitlement_summary(self, internal_user_id: str):
            return EntitlementSummary(category=EntitlementSummaryCategory.ACTIVE)

        async def get_issuance_summary(self, internal_user_id: str):
            return IssuanceOperationalSummary(state=IssuanceOperationalState.NONE)

    async def main() -> None:
        reads = _ReadsActiveNoIssuance()
        h = Adm01LookupHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            subscription=reads,
            entitlement=reads,
            issuance=reads,
            policy=_Policy(),
            redaction=None,
        )
        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))
        assert r.outcome is Adm01LookupOutcome.SUCCESS
        assert r.summary is not None
        assert (
            r.summary.support_readiness.access_readiness_bucket
            is Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_NOT_READY
        )
        assert r.summary.support_readiness.recommended_next_action is Adm01SupportNextAction.INVESTIGATE_ISSUANCE

    _run(main())


def test_adm01_lookup_issuance_failure_maps_to_safe_internal_error_bucket() -> None:
    async def main() -> None:
        reads = _Reads()
        h = Adm01LookupHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            subscription=reads,
            entitlement=reads,
            issuance=_IssuanceRaise(),
            policy=_Policy(),
            redaction=None,
        )
        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))
        assert r.outcome is Adm01LookupOutcome.SUCCESS
        assert r.summary is not None
        assert r.summary.support_readiness.telegram_identity_known is True
        assert (
            r.summary.support_readiness.access_readiness_bucket
            is Adm01SupportAccessReadinessBucket.UNKNOWN_DUE_TO_INTERNAL_ERROR
        )
        assert r.summary.support_readiness.recommended_next_action is Adm01SupportNextAction.INVESTIGATE_ISSUANCE

    _run(main())
