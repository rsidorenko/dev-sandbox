"""Pure tests: fail-closed UC-02 status mapping."""

from app.domain.status_view import map_subscription_status_view
from app.shared.types import SafeUserStatusCategory, SubscriptionSnapshotState


def test_unknown_user_needs_bootstrap() -> None:
    assert map_subscription_status_view(False, None) is SafeUserStatusCategory.NEEDS_BOOTSTRAP


def test_known_user_absent_snapshot_inactive_style() -> None:
    assert (
        map_subscription_status_view(True, SubscriptionSnapshotState.ABSENT)
        is SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE
    )


def test_needs_review() -> None:
    assert (
        map_subscription_status_view(True, SubscriptionSnapshotState.NEEDS_REVIEW)
        is SafeUserStatusCategory.NEEDS_REVIEW
    )


def test_no_paid_without_billing_backed_state() -> None:
    for state in (
        SubscriptionSnapshotState.INACTIVE,
        SubscriptionSnapshotState.NOT_ELIGIBLE,
        None,
    ):
        out = map_subscription_status_view(True, state)
        assert out is SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE


def test_subscription_active_when_billing_backed() -> None:
    assert (
        map_subscription_status_view(True, SubscriptionSnapshotState.ACTIVE)
        is SafeUserStatusCategory.SUBSCRIPTION_ACTIVE
    )
