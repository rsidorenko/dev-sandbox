"""ADM-01 policy read: derive low-cardinality policy flag from subscription snapshot semantics."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.admin_support.adm01_subscription_state_mapping import (
    Adm01SnapshotStateKind,
    classify_adm01_subscription_snapshot,
)
from app.admin_support.contracts import Adm01PolicyReadPort, AdminPolicyFlag
from app.application.interfaces import SubscriptionSnapshot


@runtime_checkable
class _SubscriptionSnapshotReader(Protocol):
    """Tests may inject fakes; production uses :class:`PostgresSubscriptionSnapshotReader`."""

    async def get_for_user(self, internal_user_id: str) -> SubscriptionSnapshot | None: ...


class Adm01SubscriptionPolicyReadAdapter(Adm01PolicyReadPort):
    """
    Map subscription snapshot state to an ADM-01 policy flag.

    Fail-closed policy:
    - missing snapshot -> UNKNOWN
    - unknown/non-enumerated state labels -> UNKNOWN
    - NEEDS_REVIEW -> ENFORCE_MANUAL_REVIEW
    - all known non-review states -> DEFAULT
    """

    def __init__(self, snapshots: _SubscriptionSnapshotReader) -> None:
        self._snapshots = snapshots

    async def get_policy_flag(self, internal_user_id: str) -> AdminPolicyFlag:
        snapshot = await self._snapshots.get_for_user(internal_user_id)
        kind = classify_adm01_subscription_snapshot(snapshot)
        if kind is Adm01SnapshotStateKind.MISSING_OR_UNKNOWN:
            return AdminPolicyFlag.UNKNOWN
        if kind is Adm01SnapshotStateKind.NEEDS_REVIEW:
            return AdminPolicyFlag.ENFORCE_MANUAL_REVIEW
        if kind in (
            Adm01SnapshotStateKind.ACTIVE,
            Adm01SnapshotStateKind.OTHER_NON_ACTIVE,
        ):
            return AdminPolicyFlag.DEFAULT

        return AdminPolicyFlag.UNKNOWN
