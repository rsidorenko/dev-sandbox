"""Отображение статуса только для чтения с fail-closed для UC-02 (без биллинга / выдачи)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from app.shared.types import SafeUserStatusCategory, SubscriptionSnapshotState

# UC-05 v1: только явные значения enum могут отображаться как подписка активна в безопасном статусе.
_BILLING_BACKED_ACTIVE: frozenset[SubscriptionSnapshotState] = frozenset({SubscriptionSnapshotState.ACTIVE})


def map_subscription_status_view(
    user_known: bool,
    snapshot: SubscriptionSnapshotState | None,
) -> SafeUserStatusCategory:
    """
    Отображает идентичность + снапшот подписки в безопасную пользовательскую категорию.

    Fail-closed: неизвестный пользователь => нужен bootstrap; отсутствующий/неизвестный снапшот => неактивный;
    нет paid/active без явного billing-backed состояния (в этом slice такого нет).
    """
    if not user_known:
        return SafeUserStatusCategory.NEEDS_BOOTSTRAP

    if snapshot is None:
        return SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE

    if snapshot is SubscriptionSnapshotState.ABSENT:
        return SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE

    if snapshot in _BILLING_BACKED_ACTIVE:
        return SafeUserStatusCategory.SUBSCRIPTION_ACTIVE

    if snapshot is SubscriptionSnapshotState.NEEDS_REVIEW:
        return SafeUserStatusCategory.NEEDS_REVIEW

    return SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE


def remaining_days(active_until_utc: datetime, *, now: datetime | None = None) -> int:
    """Whole days remaining until *active_until_utc*, rounded UP (a partial day
    counts as 1) and floored at 0 (already expired -> 0).

    Canonical 'days left' computation shared by the bot ('Осталось') and the web
    profile API so both surfaces show the identical number. Server-clock based, so
    the web consumes the API value rather than computing with the device clock.
    Matches the frontend's former ``Math.ceil((until - now) / 86400000)``."""
    now = now if now is not None else datetime.now(UTC)
    seconds = (active_until_utc - now).total_seconds()
    return max(0, math.ceil(seconds / 86_400))
