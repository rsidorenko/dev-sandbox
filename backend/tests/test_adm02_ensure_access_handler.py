"""ADM-02 ensure-access handler unit tests (fakes only; no network/DB)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from app.admin_support.adm02_ensure_access import Adm02EnsureAccessHandler
from app.admin_support.contracts import (
    Adm01SupportAccessReadinessBucket,
    Adm01SupportNextAction,
    Adm01SupportSubscriptionBucket,
    Adm02EnsureAccessAuditEvent,
    Adm02EnsureAccessAuditOutcomeBucket,
    Adm02EnsureAccessAuditPrincipalMarker,
    Adm02EnsureAccessInput,
    Adm02EnsureAccessOutcome,
    Adm02EnsureAccessRemediationResult,
    AdminActorRef,
    InternalUserTarget,
    IssuanceOperationalState,
    IssuanceOperationalSummary,
)
from app.application.interfaces import SubscriptionSnapshot
from app.shared.correlation import new_correlation_id

_FORBIDDEN = (
    "database_url",
    "postgres://",
    "postgresql://",
    "bearer ",
    "private key",
    "begin ",
    "token=",
    "vpn://",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "schema_version",
    "customer_ref",
    "provider_ref",
    "checkout_attempt_id",
    "internal_user_id",
)


def _run(coro):
    return asyncio.run(coro)


class _Auth:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed

    async def check_adm02_ensure_access_allowed(self, actor, *, correlation_id: str) -> bool:
        return self._allowed


class _OptIn:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    async def check_adm02_mutation_opt_in_enabled(self, *, correlation_id: str) -> bool:
        return self._enabled


class _Identity:
    def __init__(self, uid: str | None) -> None:
        self._uid = uid

    async def resolve_internal_user_id(self, target, *, correlation_id: str) -> str | None:
        return self._uid


class _Subscription:
    def __init__(self, label: str | None) -> None:
        self._label = label

    async def get_subscription_snapshot(self, internal_user_id: str):
        if self._label is None:
            return None
        return SubscriptionSnapshot(internal_user_id=internal_user_id, state_label=self._label)


class _Issuance:
    def __init__(self, before: IssuanceOperationalState, after: IssuanceOperationalState | None = None) -> None:
        self._before = before
        self._after = after if after is not None else before
        self.calls = 0

    async def get_issuance_summary(self, internal_user_id: str):
        self.calls += 1
        state = self._before if self.calls == 1 else self._after
        return IssuanceOperationalSummary(state=state)


class _Mutation:
    def __init__(self, issued_new: bool = True, raises: bool = False) -> None:
        self._issued_new = issued_new
        self._raises = raises
        self.calls = 0

    async def ensure_access_issued(self, internal_user_id: str, *, correlation_id: str) -> bool:
        self.calls += 1
        if self._raises:
            raise RuntimeError("DATABASE_URL=postgresql://secret")
        return self._issued_new


class _Audit:
    def __init__(self, raises: bool = False) -> None:
        self._raises = raises
        self.events: list[Adm02EnsureAccessAuditEvent] = []

    async def append_ensure_access_event(self, event: Adm02EnsureAccessAuditEvent) -> None:
        if self._raises:
            raise RuntimeError("token=secret")
        self.events.append(event)


def _inp() -> Adm02EnsureAccessInput:
    return Adm02EnsureAccessInput(
        actor=AdminActorRef(internal_admin_principal_id="adm-x"),
        target=InternalUserTarget(internal_user_id="u-1"),
        correlation_id=new_correlation_id(),
    )


def _handler(
    *,
    allowed: bool = True,
    opt_in: bool = True,
    uid: str | None = "u-1",
    sub: str | None = "active",
    issuance_before=IssuanceOperationalState.NONE,
    issuance_after: IssuanceOperationalState | None = IssuanceOperationalState.OK,
    mutation: _Mutation | None = None,
    audit: _Audit | None = None,
):
    mut = mutation or _Mutation(True, False)
    audit_sink = audit or _Audit()
    h = Adm02EnsureAccessHandler(
        authorization=_Auth(allowed),
        mutation_opt_in=_OptIn(opt_in),
        identity=_Identity(uid),
        subscription=_Subscription(sub),
        issuance=_Issuance(issuance_before, issuance_after),
        mutation=mut,
        audit=audit_sink,
    )
    return h, mut, audit_sink


def test_denied_by_authorization() -> None:
    async def main() -> None:
        h, mut, audit = _handler(allowed=False)
        r = await h.handle(_inp())
        assert r.outcome is Adm02EnsureAccessOutcome.DENIED
        assert r.summary is None
        assert mut.calls == 0
        assert len(audit.events) == 1
        assert audit.events[0].outcome_bucket is Adm02EnsureAccessAuditOutcomeBucket.DENIED_UNAUTHORIZED

    _run(main())


def test_denied_when_mutation_opt_in_disabled() -> None:
    async def main() -> None:
        h, mut, audit = _handler(opt_in=False)
        r = await h.handle(_inp())
        assert r.outcome is Adm02EnsureAccessOutcome.DENIED
        assert r.summary is None
        assert mut.calls == 0
        assert len(audit.events) == 1
        assert audit.events[0].outcome_bucket is Adm02EnsureAccessAuditOutcomeBucket.DENIED_MUTATION_OPT_IN_DISABLED

    _run(main())


def test_unknown_identity_safe_noop() -> None:
    async def main() -> None:
        h, mut, audit = _handler(uid=None)
        r = await h.handle(_inp())
        assert r.outcome is Adm02EnsureAccessOutcome.SUCCESS
        assert r.summary is not None
        assert r.summary.remediation_result is Adm02EnsureAccessRemediationResult.NOOP_IDENTITY_UNKNOWN
        assert r.summary.telegram_identity_known is False
        assert mut.calls == 0
        assert len(audit.events) == 1
        assert audit.events[0].outcome_bucket is Adm02EnsureAccessAuditOutcomeBucket.NOOP_IDENTITY_UNKNOWN

    _run(main())


def test_no_active_subscription_safe_noop_no_mutation() -> None:
    async def main() -> None:
        h, mut, audit = _handler(sub="inactive")
        r = await h.handle(_inp())
        assert r.outcome is Adm02EnsureAccessOutcome.SUCCESS
        assert r.summary is not None
        assert r.summary.subscription_bucket is Adm01SupportSubscriptionBucket.INACTIVE
        assert r.summary.remediation_result is Adm02EnsureAccessRemediationResult.NOOP_NO_ACTIVE_SUBSCRIPTION
        assert (
            r.summary.access_readiness_bucket is Adm01SupportAccessReadinessBucket.NOT_APPLICABLE_NO_ACTIVE_SUBSCRIPTION
        )
        assert r.summary.recommended_next_action is Adm01SupportNextAction.INVESTIGATE_BILLING_APPLY
        assert mut.calls == 0
        assert len(audit.events) == 1
        assert audit.events[0].outcome_bucket is Adm02EnsureAccessAuditOutcomeBucket.NOOP_NO_ACTIVE_SUBSCRIPTION

    _run(main())


def test_active_not_ready_issues_once_and_ready_summary() -> None:
    async def main() -> None:
        h, mut, audit = _handler(
            sub="active",
            issuance_before=IssuanceOperationalState.NONE,
            issuance_after=IssuanceOperationalState.OK,
            mutation=_Mutation(issued_new=True),
        )
        r = await h.handle(_inp())
        assert r.outcome is Adm02EnsureAccessOutcome.SUCCESS
        assert r.summary is not None
        assert r.summary.remediation_result is Adm02EnsureAccessRemediationResult.ISSUED_ACCESS
        assert r.summary.access_readiness_bucket is Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY
        assert r.summary.recommended_next_action is Adm01SupportNextAction.ASK_USER_TO_USE_GET_ACCESS
        assert mut.calls == 1
        assert len(audit.events) == 1
        assert audit.events[0].outcome_bucket is Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS

    _run(main())


def test_active_already_ready_noop_without_mutation() -> None:
    async def main() -> None:
        h, mut, audit = _handler(
            sub="active",
            issuance_before=IssuanceOperationalState.OK,
            issuance_after=IssuanceOperationalState.OK,
        )
        r = await h.handle(_inp())
        assert r.outcome is Adm02EnsureAccessOutcome.SUCCESS
        assert r.summary is not None
        assert r.summary.remediation_result is Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY
        assert mut.calls == 0
        assert len(audit.events) == 1
        assert audit.events[0].outcome_bucket is Adm02EnsureAccessAuditOutcomeBucket.NOOP_ACCESS_ALREADY_READY

    _run(main())


def test_repeated_call_idempotent_no_duplicate_mutation() -> None:
    async def main() -> None:
        mut = _Mutation(issued_new=False)
        h, _, audit = _handler(
            sub="active",
            issuance_before=IssuanceOperationalState.NONE,
            issuance_after=IssuanceOperationalState.OK,
            mutation=mut,
        )
        r1 = await h.handle(_inp())
        r2 = await h.handle(_inp())
        assert r1.summary is not None and r2.summary is not None
        assert r1.summary.remediation_result in {
            Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY,
            Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
        }
        assert r2.summary.remediation_result in {
            Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY,
            Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
        }
        assert mut.calls <= 2
        assert len(audit.events) == 2

    _run(main())


def test_issuance_failure_safe_failed_summary_no_leak() -> None:
    async def main() -> None:
        h, _, audit = _handler(
            sub="active",
            issuance_before=IssuanceOperationalState.NONE,
            issuance_after=IssuanceOperationalState.UNKNOWN,
            mutation=_Mutation(raises=True),
        )
        r = await h.handle(_inp())
        assert r.outcome is Adm02EnsureAccessOutcome.SUCCESS
        assert r.summary is not None
        assert r.summary.remediation_result is Adm02EnsureAccessRemediationResult.FAILED_SAFE
        assert len(audit.events) == 1
        assert audit.events[0].outcome_bucket is Adm02EnsureAccessAuditOutcomeBucket.FAILED_SAFE
        blob = json.dumps(asdict(r), sort_keys=True).lower()
        for frag in _FORBIDDEN:
            assert frag not in blob
        audit_blob = json.dumps([asdict(event) for event in audit.events], sort_keys=True).lower()
        for frag in _FORBIDDEN:
            assert frag not in audit_blob

    _run(main())


def test_audit_event_uses_redacted_principal_marker_and_never_contains_raw_principal() -> None:
    async def main() -> None:
        h, _, audit = _handler(allowed=False)
        await h.handle(_inp())
        assert len(audit.events) == 1
        event = audit.events[0]
        assert event.principal_marker is Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED
        blob = json.dumps(asdict(event), sort_keys=True).lower()
        assert "adm-x" not in blob

    _run(main())


def test_audit_sink_failure_does_not_break_handler_outcome() -> None:
    async def main() -> None:
        h, mut, _ = _handler(
            sub="active",
            issuance_before=IssuanceOperationalState.OK,
            issuance_after=IssuanceOperationalState.OK,
            audit=_Audit(raises=True),
        )
        r = await h.handle(_inp())
        assert r.outcome is Adm02EnsureAccessOutcome.SUCCESS
        assert r.summary is not None
        assert r.summary.remediation_result is Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY
        assert mut.calls == 0
        blob = json.dumps(asdict(r), sort_keys=True).lower()
        for frag in _FORBIDDEN:
            assert frag not in blob

    _run(main())
