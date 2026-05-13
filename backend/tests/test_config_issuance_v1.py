"""Config issuance v1: entitlement, fake provider, in-process idempotency (no I/O)."""

from __future__ import annotations

import pytest

from app.domain.status_view import map_subscription_status_view
from app.issuance.contracts import (
    IssuanceOperationType,
    IssuanceOutcomeCategory,
    IssuanceRequest,
    IssuanceServiceResult,
)
from app.issuance.entitlement import issue_resend_denial_category, subscription_allows_issue_resend
from app.issuance.fake_provider import FakeIssuanceProvider, FakeProviderMode
from app.issuance.service import IssuanceService
from app.shared.correlation import new_correlation_id
from app.shared.types import SafeUserStatusCategory, SubscriptionSnapshotState

_FORBIDDEN = (
    "PRIVATE KEY",
    "BEGIN ",
    "token=",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "vpn://",
)


def _assert_no_forbidden_secrets(s: str) -> None:
    u = s.upper()
    for frag in _FORBIDDEN:
        assert frag not in u


def _req(
    *,
    op: IssuanceOperationType,
    sub: SubscriptionSnapshotState | None,
    idem: str,
    link: str | None = None,
) -> IssuanceRequest:
    return IssuanceRequest(
        internal_user_id="user-1",
        subscription_state=sub,
        operation=op,
        idempotency_key=idem,
        correlation_id=new_correlation_id(),
        link_issue_idempotency_key=link,
    )


@pytest.mark.parametrize("state", list(SubscriptionSnapshotState) + [None])
def test_gate_issue_resend_matches_status_view(state: SubscriptionSnapshotState | None) -> None:
    allows = subscription_allows_issue_resend(state)
    view = map_subscription_status_view(True, state)
    expect_active = view is SafeUserStatusCategory.SUBSCRIPTION_ACTIVE
    assert allows is expect_active
    if not allows:
        assert issue_resend_denial_category(state) in (
            IssuanceOutcomeCategory.NOT_ENTITLED,
            IssuanceOutcomeCategory.NEEDS_REVIEW,
        )
        if state is SubscriptionSnapshotState.NEEDS_REVIEW:
            assert issue_resend_denial_category(state) is IssuanceOutcomeCategory.NEEDS_REVIEW
        else:
            assert issue_resend_denial_category(state) is IssuanceOutcomeCategory.NOT_ENTITLED


@pytest.mark.asyncio
async def test_active_success_issued() -> None:
    p = FakeIssuanceProvider(FakeProviderMode.SUCCESS)
    svc = IssuanceService(p)
    r = await svc.execute(
        _req(
            op=IssuanceOperationType.ISSUE,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="ik-issue-1",
        )
    )
    assert r.category is IssuanceOutcomeCategory.ISSUED
    assert r.safe_ref is not None
    _assert_no_forbidden_secrets(r.safe_ref)
    assert p.create_or_ensure_calls == 1
    for rec in svc.audit_records:
        _assert_no_forbidden_secrets(rec.redacted_summary())


@pytest.mark.asyncio
async def test_inactive_no_provider_call() -> None:
    p = FakeIssuanceProvider(FakeProviderMode.SUCCESS)
    svc = IssuanceService(p)
    r = await svc.execute(
        _req(
            op=IssuanceOperationType.ISSUE,
            sub=SubscriptionSnapshotState.INACTIVE,
            idem="ik-1",
        )
    )
    assert r.category is IssuanceOutcomeCategory.NOT_ENTITLED
    assert p.create_or_ensure_calls == 0
    r2 = await svc.execute(
        _req(
            op=IssuanceOperationType.ISSUE,
            sub=SubscriptionSnapshotState.NOT_ELIGIBLE,
            idem="ik-2",
        )
    )
    assert r2.category is IssuanceOutcomeCategory.NOT_ENTITLED
    assert p.create_or_ensure_calls == 0


@pytest.mark.asyncio
async def test_needs_review_no_provider() -> None:
    p = FakeIssuanceProvider(FakeProviderMode.SUCCESS)
    svc = IssuanceService(p)
    r = await svc.execute(
        _req(
            op=IssuanceOperationType.ISSUE,
            sub=SubscriptionSnapshotState.NEEDS_REVIEW,
            idem="ik-nr",
        )
    )
    assert r.category is IssuanceOutcomeCategory.NEEDS_REVIEW
    assert p.create_or_ensure_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("sub", [None, SubscriptionSnapshotState.ABSENT])
async def test_missing_or_absent_fail_closed(sub: SubscriptionSnapshotState | None) -> None:
    p = FakeIssuanceProvider(FakeProviderMode.SUCCESS)
    svc = IssuanceService(p)
    r = await svc.execute(_req(op=IssuanceOperationType.ISSUE, sub=sub, idem="ik-m"))
    assert r.category is IssuanceOutcomeCategory.NOT_ENTITLED
    assert p.create_or_ensure_calls == 0


@pytest.mark.asyncio
async def test_provider_unavailable() -> None:
    p = FakeIssuanceProvider(FakeProviderMode.UNAVAILABLE)
    svc = IssuanceService(p)
    r = await svc.execute(
        _req(
            op=IssuanceOperationType.ISSUE,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="ik-u",
        )
    )
    assert r.category is IssuanceOutcomeCategory.PROVIDER_UNAVAILABLE
    assert p.create_or_ensure_calls == 1


@pytest.mark.asyncio
async def test_provider_rejected() -> None:
    p = FakeIssuanceProvider(FakeProviderMode.REJECTED)
    svc = IssuanceService(p)
    r = await svc.execute(
        _req(
            op=IssuanceOperationType.ISSUE,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="ik-rj",
        )
    )
    assert r.category is IssuanceOutcomeCategory.PROVIDER_REJECTED


@pytest.mark.asyncio
async def test_provider_unknown_not_issued() -> None:
    p = FakeIssuanceProvider(FakeProviderMode.UNKNOWN)
    svc = IssuanceService(p)
    r = await svc.execute(
        _req(
            op=IssuanceOperationType.ISSUE,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="ik-uk",
        )
    )
    assert r.category is IssuanceOutcomeCategory.INTERNAL_ERROR
    r2 = await svc.execute(
        _req(
            op=IssuanceOperationType.ISSUE,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="ik-uk",
        )
    )
    assert r2.category is IssuanceOutcomeCategory.INTERNAL_ERROR
    assert p.create_or_ensure_calls == 2


@pytest.mark.asyncio
async def test_issue_idempotency_no_duplicate_provider() -> None:
    p = FakeIssuanceProvider(FakeProviderMode.SUCCESS)
    svc = IssuanceService(p)
    ikey = "idem-issue-same"
    a = await svc.execute(_req(op=IssuanceOperationType.ISSUE, sub=SubscriptionSnapshotState.ACTIVE, idem=ikey))
    b = await svc.execute(_req(op=IssuanceOperationType.ISSUE, sub=SubscriptionSnapshotState.ACTIVE, idem=ikey))
    assert a.category is IssuanceOutcomeCategory.ISSUED
    assert b.category is IssuanceOutcomeCategory.ALREADY_ISSUED
    assert a.safe_ref == b.safe_ref
    assert p.create_or_ensure_calls == 1


@pytest.mark.asyncio
async def test_resend_idempotent_no_duplicate_get_safe() -> None:
    p = FakeIssuanceProvider(FakeProviderMode.SUCCESS)
    svc = IssuanceService(p)
    ikey = "ik-origin"
    await svc.execute(
        _req(
            op=IssuanceOperationType.ISSUE,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem=ikey,
        )
    )
    r1 = await svc.execute(
        _req(
            op=IssuanceOperationType.RESEND,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="resend-1",
            link=ikey,
        )
    )
    r2 = await svc.execute(
        _req(
            op=IssuanceOperationType.RESEND,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="resend-1",
            link=ikey,
        )
    )
    assert r1.category is IssuanceOutcomeCategory.DELIVERY_READY
    assert r2.category is IssuanceOutcomeCategory.DELIVERY_READY
    assert r1.safe_ref == r2.safe_ref
    assert p.get_safe_delivery_calls == 1


@pytest.mark.asyncio
async def test_resend_unsafe_if_never_issued() -> None:
    p = FakeIssuanceProvider(FakeProviderMode.SUCCESS)
    svc = IssuanceService(p)
    r = await svc.execute(
        _req(
            op=IssuanceOperationType.RESEND,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="rs-orphan",
            link="no-such-issue",
        )
    )
    assert r.category is IssuanceOutcomeCategory.UNSAFE_TO_DELIVER
    assert p.get_safe_delivery_calls == 0


@pytest.mark.asyncio
async def test_revoke_idempotent() -> None:
    p = FakeIssuanceProvider(FakeProviderMode.SUCCESS)
    svc = IssuanceService(p)
    ikey = "to-revoke"
    await svc.execute(
        _req(
            op=IssuanceOperationType.ISSUE,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem=ikey,
        )
    )
    rk = "revoke-dedup"
    a = await svc.execute(
        _req(
            op=IssuanceOperationType.REVOKE,
            sub=SubscriptionSnapshotState.INACTIVE,
            idem=rk,
            link=ikey,
        )
    )
    b = await svc.execute(
        _req(
            op=IssuanceOperationType.REVOKE,
            sub=SubscriptionSnapshotState.INACTIVE,
            idem=rk,
            link=ikey,
        )
    )
    assert a.category is IssuanceOutcomeCategory.REVOKED
    assert b.category is IssuanceOutcomeCategory.REVOKED
    assert p.revoke_access_calls == 1


@pytest.mark.asyncio
async def test_revoke_second_idempotency_key_ledger_only() -> None:
    """If ledger already revoked, a new revoke id shows REVOKED without a second provider call."""
    p = FakeIssuanceProvider(FakeProviderMode.SUCCESS)
    svc = IssuanceService(p)
    ikey = "k-rev-2"
    await svc.execute(_req(op=IssuanceOperationType.ISSUE, sub=SubscriptionSnapshotState.ACTIVE, idem=ikey))
    await svc.execute(
        _req(
            op=IssuanceOperationType.REVOKE,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="r-a",
            link=ikey,
        )
    )
    c = await svc.execute(
        _req(
            op=IssuanceOperationType.REVOKE,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="r-b",
            link=ikey,
        )
    )
    assert c.category is IssuanceOutcomeCategory.REVOKED
    assert p.revoke_access_calls == 1


def test_result_and_audit_no_secret_substrings() -> None:
    r = IssuanceServiceResult(category=IssuanceOutcomeCategory.ISSUED, safe_ref="issuance-ref:fake:x")
    _assert_no_forbidden_secrets(f"{r.category!s} {r.safe_ref!s}")


@pytest.mark.asyncio
async def test_resend_fails_if_revoked() -> None:
    p = FakeIssuanceProvider(FakeProviderMode.SUCCESS)
    svc = IssuanceService(p)
    ikey = "k-rev-deny-resend"
    await svc.execute(_req(op=IssuanceOperationType.ISSUE, sub=SubscriptionSnapshotState.ACTIVE, idem=ikey))
    await svc.execute(
        _req(
            op=IssuanceOperationType.REVOKE,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="r1",
            link=ikey,
        )
    )
    o = await svc.execute(
        _req(
            op=IssuanceOperationType.RESEND,
            sub=SubscriptionSnapshotState.ACTIVE,
            idem="rs1",
            link=ikey,
        )
    )
    assert o.category is IssuanceOutcomeCategory.REVOKED
    assert p.get_safe_delivery_calls == 0
