"""Unit tests for :class:`Adm01SubscriptionEntitlementReadAdapter` (no DB/network)."""

from __future__ import annotations

import pytest

from app.admin_support.adm01_subscription_entitlement_read_adapter import (
    Adm01SubscriptionEntitlementReadAdapter,
)
from app.admin_support.contracts import EntitlementSummaryCategory
from app.application.interfaces import SubscriptionSnapshot
from app.security.errors import InternalErrorCategory, PersistenceDependencyError

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
    "raw-billing-payload",
)


class _FakeSnapshots:
    def __init__(self, current: SubscriptionSnapshot | None) -> None:
        self._current = current

    async def get_for_user(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        del internal_user_id
        return self._current


class _ErrorSnapshots:
    def __init__(self, err: Exception) -> None:
        self._err = err

    async def get_for_user(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        del internal_user_id
        raise self._err


def _assert_no_forbidden_markers(text: str) -> None:
    for marker in _FORBIDDEN_MARKERS:
        assert marker not in text


@pytest.mark.asyncio
async def test_missing_snapshot_maps_to_unknown_fail_closed() -> None:
    a = Adm01SubscriptionEntitlementReadAdapter(_FakeSnapshots(None))
    out = await a.get_entitlement_summary("u-1")
    assert out.category is EntitlementSummaryCategory.UNKNOWN
    _assert_no_forbidden_markers(repr(out))


@pytest.mark.asyncio
async def test_active_snapshot_maps_to_active() -> None:
    a = Adm01SubscriptionEntitlementReadAdapter(
        _FakeSnapshots(SubscriptionSnapshot(internal_user_id="u-1", state_label="active")),
    )
    out = await a.get_entitlement_summary("u-1")
    assert out.category is EntitlementSummaryCategory.ACTIVE


@pytest.mark.asyncio
async def test_inactive_and_absent_and_not_eligible_map_to_inactive() -> None:
    for label in ("inactive", "absent", "not_eligible"):
        a = Adm01SubscriptionEntitlementReadAdapter(
            _FakeSnapshots(SubscriptionSnapshot(internal_user_id="u-1", state_label=label)),
        )
        out = await a.get_entitlement_summary("u-1")
        assert out.category is EntitlementSummaryCategory.INACTIVE


@pytest.mark.asyncio
async def test_needs_review_maps_to_inactive_non_active_fail_closed() -> None:
    a = Adm01SubscriptionEntitlementReadAdapter(
        _FakeSnapshots(SubscriptionSnapshot(internal_user_id="u-1", state_label="needs_review")),
    )
    out = await a.get_entitlement_summary("u-1")
    assert out.category is EntitlementSummaryCategory.INACTIVE


@pytest.mark.asyncio
async def test_unknown_snapshot_label_maps_to_unknown() -> None:
    a = Adm01SubscriptionEntitlementReadAdapter(
        _FakeSnapshots(SubscriptionSnapshot(internal_user_id="u-1", state_label="unexpected_state")),
    )
    out = await a.get_entitlement_summary("u-1")
    assert out.category is EntitlementSummaryCategory.UNKNOWN


@pytest.mark.asyncio
async def test_persistence_error_propagates() -> None:
    a = Adm01SubscriptionEntitlementReadAdapter(
        _ErrorSnapshots(
            PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT),
        ),
    )
    with pytest.raises(PersistenceDependencyError) as e:
        await a.get_entitlement_summary("u-1")
    assert e.value.category is InternalErrorCategory.PERSISTENCE_TRANSIENT
