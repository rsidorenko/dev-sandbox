"""Trial period domain logic: 3-day free VPN for new users."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

TRIAL_DURATION_DAYS = 3


def trial_expires_at(started_at: datetime) -> datetime:
    return started_at + timedelta(days=TRIAL_DURATION_DAYS)


def is_trial_active(
    *,
    trial_started_at: datetime | None,
    trial_expires_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(UTC)
    if trial_started_at is None or trial_expires_at is None:
        return False
    return trial_started_at <= now < trial_expires_at


def trial_expires_soon(
    *,
    trial_expires_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """True if trial expires within the next 24 hours."""
    now = now or datetime.now(UTC)
    if trial_expires_at is None:
        return False
    remaining = trial_expires_at - now
    return timedelta(0) < remaining <= timedelta(hours=24)


def trial_expired(
    *,
    trial_expires_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(UTC)
    if trial_expires_at is None:
        return False
    return now >= trial_expires_at
