"""Combined adapter-level mapping equivalence test for ADM-01 snapshot semantics."""

from __future__ import annotations

import pytest

from app.admin_support.adm01_subscription_entitlement_read_adapter import (
    Adm01SubscriptionEntitlementReadAdapter,
)
from app.admin_support.adm01_subscription_policy_read_adapter import (
    Adm01SubscriptionPolicyReadAdapter,
)
from app.admin_support.contracts import AdminPolicyFlag, EntitlementSummaryCategory
from app.application.interfaces import SubscriptionSnapshot

_FORBIDDEN_MARKERS = (
    "provider_issuance_ref",
    "issue_idempotency_key",
    "internal_fact_ref",
    "external_event_id",
    "DATABASE_URL",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "PRIVATE KEY",
)


class _FakeSnapshots:
    def __init__(self, current: SubscriptionSnapshot | None) -> None:
        self._current = current

    async def get_for_user(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        del internal_user_id
        return self._current


def _assert_no_forbidden_markers(text: str) -> None:
    for marker in _FORBIDDEN_MARKERS:
        assert marker not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_label", "expected_entitlement", "expected_policy"),
    [
        (None, EntitlementSummaryCategory.UNKNOWN, AdminPolicyFlag.UNKNOWN),
        ("active", EntitlementSummaryCategory.ACTIVE, AdminPolicyFlag.DEFAULT),
        ("inactive", EntitlementSummaryCategory.INACTIVE, AdminPolicyFlag.DEFAULT),
        ("absent", EntitlementSummaryCategory.INACTIVE, AdminPolicyFlag.DEFAULT),
        ("not_eligible", EntitlementSummaryCategory.INACTIVE, AdminPolicyFlag.DEFAULT),
        (
            "needs_review",
            EntitlementSummaryCategory.INACTIVE,
            AdminPolicyFlag.ENFORCE_MANUAL_REVIEW,
        ),
        ("unexpected_state", EntitlementSummaryCategory.UNKNOWN, AdminPolicyFlag.UNKNOWN),
    ],
)
async def test_adapters_mapping_equivalence_matrix(
    state_label: str | None,
    expected_entitlement: EntitlementSummaryCategory,
    expected_policy: AdminPolicyFlag,
) -> None:
    snapshot = None if state_label is None else SubscriptionSnapshot(internal_user_id="u-1", state_label=state_label)
    fake_snapshots = _FakeSnapshots(snapshot)
    entitlement_adapter = Adm01SubscriptionEntitlementReadAdapter(fake_snapshots)
    policy_adapter = Adm01SubscriptionPolicyReadAdapter(fake_snapshots)

    entitlement_summary = await entitlement_adapter.get_entitlement_summary("u-1")
    policy_flag = await policy_adapter.get_policy_flag("u-1")

    assert entitlement_summary.category is expected_entitlement
    assert policy_flag is expected_policy

    rendered = " | ".join(
        (
            repr(entitlement_summary),
            repr(entitlement_summary.category),
            repr(policy_flag),
            str(entitlement_summary.category),
            str(policy_flag),
        ),
    )
    _assert_no_forbidden_markers(rendered)
