"""Регрессии конверсионных этапов жизненного цикла триала."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from app.bot_transport.notification_texts import (
    text_trial_expired,
    text_trial_expired_followup,
    text_trial_expiring_6h,
    text_trial_expiring_24h,
)
from app.bot_transport.storefront_ui import CB_BUY_VPN
from app.runtime.notification_scheduler import NotificationScheduler


class _Pool:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        self.fetch_calls.append((sql, args))
        return self.rows


def _scheduler(pool: _Pool) -> NotificationScheduler:
    scheduler = NotificationScheduler(pool=pool, bot_token="test", vless_provider=AsyncMock())
    scheduler._send_notification = AsyncMock(return_value=True)
    return scheduler


def _run(awaitable):
    return asyncio.run(awaitable)


def test_regular_warning_uses_non_overlapping_window_and_legacy_dedup() -> None:
    pool = _Pool([{"internal_user_id": "u1", "trial_expires_at": datetime.now(UTC)}])
    scheduler = _scheduler(pool)

    _run(scheduler._check_trial_expiring(datetime.now(UTC)))

    sql, args = pool.fetch_calls[0]
    assert "COALESCE(s.active_until_utc, s.trial_expires_at)" in sql
    assert "<= NOW() + make_interval(hours => $1)" in sql
    assert "> NOW() + make_interval(hours => $2)" in sql
    assert "NOT EXISTS" in sql
    assert args == (24, 6, ["trial_expiring", "trial_expiring_24h"])
    scheduler._send_notification.assert_awaited_once()
    assert scheduler._send_notification.await_args.kwargs["notification_type"] == "trial_expiring_24h"


def test_urgent_warning_uses_last_six_hours_and_once_ever_dedup() -> None:
    pool = _Pool([{"internal_user_id": "u2", "trial_expires_at": datetime.now(UTC)}])
    scheduler = _scheduler(pool)

    _run(scheduler._check_trial_expiring_urgent(datetime.now(UTC)))

    sql, args = pool.fetch_calls[0]
    assert "COALESCE(s.active_until_utc, s.trial_expires_at) > NOW()" in sql
    assert "<= NOW() + make_interval(hours => $1)" in sql
    assert "NOT EXISTS" in sql
    assert args == (6, "trial_expiring_6h")
    scheduler._send_notification.assert_awaited_once()
    assert scheduler._send_notification.await_args.kwargs["notification_type"] == "trial_expiring_6h"


def test_paywall_requires_recent_key_deactivation_and_retries_without_log() -> None:
    pool = _Pool([{"internal_user_id": "u3"}])
    scheduler = _scheduler(pool)

    _run(scheduler._check_trial_expired_paywall(datetime.now(UTC)))

    sql, args = pool.fetch_calls[0]
    assert "s.state_label = 'expired'" in sql
    assert "s.keys_deactivated_at IS NOT NULL" in sql
    assert "s.keys_deactivated_at >= NOW() - make_interval(hours => $1)" in sql
    assert "NOT EXISTS" in sql
    assert args == (48, "trial_expired")
    assert scheduler._send_notification.await_args.kwargs["notification_type"] == "trial_expired"


def test_followup_is_bounded_to_24_48_hours_and_excludes_paid_users() -> None:
    pool = _Pool([{"internal_user_id": "u4"}])
    scheduler = _scheduler(pool)

    _run(scheduler._check_trial_expired_followup(datetime.now(UTC)))

    sql, args = pool.fetch_calls[0]
    assert "s.plan_id IS NULL" in sql
    assert "s.keys_deactivated_at <= NOW() - make_interval(hours => $1)" in sql
    assert "s.keys_deactivated_at > NOW() - make_interval(hours => $2)" in sql
    assert "NOT EXISTS" in sql
    assert args == (24, 48, "trial_expired_followup_24h")
    assert scheduler._send_notification.await_args.kwargs["notification_type"] == "trial_expired_followup_24h"


def test_empty_stage_does_not_attempt_delivery() -> None:
    pool = _Pool()
    scheduler = _scheduler(pool)

    _run(scheduler._check_trial_expiring_urgent(datetime.now(UTC)))

    scheduler._send_notification.assert_not_awaited()


def test_trial_messages_keep_prices_dynamic_and_lead_to_existing_catalog() -> None:
    messages = (
        text_trial_expiring_24h(),
        text_trial_expiring_6h(),
        text_trial_expired(),
        text_trial_expired_followup(),
    )

    for text, keyboard in messages:
        assert "₽" not in text
        assert keyboard["inline_keyboard"][0][0]["callback_data"] == CB_BUY_VPN

    assert "без повторной установки" in messages[2][0]
    assert "настройки сохранены" in messages[3][0]
