"""Pure tests: fail-closed UC-02 status mapping."""

from datetime import UTC, datetime, timedelta

from app.domain.status_view import map_subscription_status_view, remaining_days
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


# --- remaining_days: canonical 'days left' shared by bot + web ---


def test_remaining_days_rounds_up_partial_day() -> None:
    now = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    # 1.5 days ahead -> ceil -> 2 (the bot's old date-diff could give 1 here)
    until = now + timedelta(days=1, hours=12)
    assert remaining_days(until, now=now) == 2


def test_remaining_days_exact_whole_day() -> None:
    now = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    assert remaining_days(now + timedelta(days=3), now=now) == 3


def test_remaining_days_small_fraction_is_one() -> None:
    now = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    # 2 hours left -> still "1 day"
    assert remaining_days(now + timedelta(hours=2), now=now) == 1


def test_remaining_days_expired_floors_at_zero() -> None:
    now = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    assert remaining_days(now - timedelta(days=1), now=now) == 0
    # just expired (a few seconds ago) -> 0, not negative
    assert remaining_days(now - timedelta(seconds=5), now=now) == 0
