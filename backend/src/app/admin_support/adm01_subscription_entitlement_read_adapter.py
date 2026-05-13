"""ADM-01 entitlement read: derive low-cardinality entitlement from subscription snapshot semantics."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.admin_support.adm01_subscription_state_mapping import (
    Adm01SnapshotStateKind,
    classify_adm01_subscription_snapshot,
)
from app.admin_support.contracts import (
    Adm01EntitlementReadPort,
    EntitlementSummary,
    EntitlementSummaryCategory,
)
from app.application.interfaces import SubscriptionSnapshot


@runtime_checkable
class _SubscriptionSnapshotReader(Protocol):
    """Tests may inject fakes; production uses :class:`PostgresSubscriptionSnapshotReader`."""

    async def get_for_user(self, internal_user_id: str) -> SubscriptionSnapshot | None: ...


class Adm01SubscriptionEntitlementReadAdapter(Adm01EntitlementReadPort):
    """
    Map subscription snapshot state to an ADM-01 entitlement category.

    Fail-closed policy:
    - missing snapshot -> UNKNOWN
    - unknown/non-enumerated state labels -> UNKNOWN
    - only explicit ACTIVE maps to ACTIVE
    - all known non-active states map to INACTIVE
    """

    def __init__(self, snapshots: _SubscriptionSnapshotReader) -> None:
        self._snapshots = snapshots

    async def get_entitlement_summary(self, internal_user_id: str) -> EntitlementSummary:
        snapshot = await self._snapshots.get_for_user(internal_user_id)
        kind = classify_adm01_subscription_snapshot(snapshot)
        if kind is Adm01SnapshotStateKind.MISSING_OR_UNKNOWN:
            return EntitlementSummary(category=EntitlementSummaryCategory.UNKNOWN)
        if kind is Adm01SnapshotStateKind.ACTIVE:
            return EntitlementSummary(category=EntitlementSummaryCategory.ACTIVE)
        if kind in (
            Adm01SnapshotStateKind.NEEDS_REVIEW,
            Adm01SnapshotStateKind.OTHER_NON_ACTIVE,
        ):
            return EntitlementSummary(category=EntitlementSummaryCategory.INACTIVE)

        return EntitlementSummary(category=EntitlementSummaryCategory.UNKNOWN)
