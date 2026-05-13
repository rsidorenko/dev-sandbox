"""Internal ADM-01 snapshot classification helper (low-cardinality, fail-closed)."""

from __future__ import annotations

from enum import StrEnum

from app.application.interfaces import SubscriptionSnapshot
from app.shared.types import SubscriptionSnapshotState


class Adm01SnapshotStateKind(StrEnum):
    """Internal classification for ADM-01 adapter mapping only."""

    MISSING_OR_UNKNOWN = "missing_or_unknown"
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"
    OTHER_NON_ACTIVE = "other_non_active"


def classify_adm01_subscription_snapshot(
    snapshot: SubscriptionSnapshot | None,
) -> Adm01SnapshotStateKind:
    """Classify raw snapshot into bounded internal ADM-01 mapping buckets."""
    if snapshot is None:
        return Adm01SnapshotStateKind.MISSING_OR_UNKNOWN

    try:
        state = SubscriptionSnapshotState(snapshot.state_label)
    except ValueError:
        return Adm01SnapshotStateKind.MISSING_OR_UNKNOWN

    if state is SubscriptionSnapshotState.ACTIVE:
        return Adm01SnapshotStateKind.ACTIVE
    if state is SubscriptionSnapshotState.NEEDS_REVIEW:
        return Adm01SnapshotStateKind.NEEDS_REVIEW
    if state in (
        SubscriptionSnapshotState.ABSENT,
        SubscriptionSnapshotState.INACTIVE,
        SubscriptionSnapshotState.NOT_ELIGIBLE,
    ):
        return Adm01SnapshotStateKind.OTHER_NON_ACTIVE
    return Adm01SnapshotStateKind.MISSING_OR_UNKNOWN
