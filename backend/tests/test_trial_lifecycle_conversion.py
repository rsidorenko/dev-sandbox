"""Регрессии конверсионных этапов жизненного цикла триала."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.bot_transport.notification_texts import (
    text_checkout_abandoned,
    text_trial_activation_nudge,
    text_trial_connection_nudge,
    text_trial_expired,
    text_trial_expired_followup,
    text_trial_expiring_6h,
    text_trial_expiring_24h,
)
from app.bot_transport.storefront_ui import (
    CB_BUY_VPN,
    CB_CONNECT_DEVICE,
    CB_CONNECT_FAST_ANDROID,
    CB_CONNECT_FAST_IOS,
    CB_CONNECT_FAST_MAC,
    CB_CONNECT_FAST_WIN,
    CB_TRIAL,
)
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
    pool = _Pool(
        [
            {
                "internal_user_id": "u1",
                "access_expires_at": datetime.now(UTC),
                "has_traffic": True,
                "platform": "android",
            }
        ]
    )
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
    pool = _Pool(
        [
            {
                "internal_user_id": "u2",
                "access_expires_at": datetime.now(UTC),
                "has_traffic": True,
                "platform": "android",
            }
        ]
    )
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

    assert "подключать всё заново не придётся" in messages[2][0]
    assert "Подключение сохранено" in messages[3][0]


def test_early_conversion_messages_use_existing_routes() -> None:
    activation_text, activation_keyboard = text_trial_activation_nudge()
    connection_text, connection_keyboard = text_trial_connection_nudge(platform="android")
    fallback_text, fallback_keyboard = text_trial_connection_nudge(platform="")
    checkout_text, checkout_keyboard = text_checkout_abandoned(plan_id="3m", device_count=8)

    assert "без оплаты и привязки карты" in activation_text
    assert activation_keyboard["inline_keyboard"][0][0]["callback_data"] == CB_TRIAL
    assert "последний шаг" in connection_text
    assert connection_keyboard["inline_keyboard"][0][0]["callback_data"] == CB_CONNECT_FAST_ANDROID
    assert fallback_keyboard["inline_keyboard"][0][0]["callback_data"] == CB_CONNECT_DEVICE
    assert fallback_text == connection_text
    assert "Оплата не была завершена" in checkout_text
    assert "3 месяца, 8 устройств" in checkout_text
    assert checkout_keyboard["inline_keyboard"][0][0]["callback_data"] == "confirm_pay:3m:8"

    _, legacy_checkout_keyboard = text_checkout_abandoned()
    assert legacy_checkout_keyboard["inline_keyboard"][0][0]["callback_data"] == CB_BUY_VPN


def test_trial_expiry_cta_depends_on_real_traffic() -> None:
    paid_text, paid_keyboard = text_trial_expiring_24h(
        active_until="15.09 в 12:30",
        has_traffic=True,
        platform="android",
    )
    connect_text, connect_keyboard = text_trial_expiring_24h(
        active_until="15.09 в 12:30",
        has_traffic=False,
        platform="android",
    )

    assert "15.09 в 12:30" in paid_text
    assert paid_keyboard["inline_keyboard"][0][0]["callback_data"] == CB_BUY_VPN
    assert "ещё не удалось проверить" in connect_text
    assert connect_keyboard["inline_keyboard"][0][0]["callback_data"] == CB_CONNECT_FAST_ANDROID


@pytest.mark.parametrize(
    ("platform", "expected_callback"),
    [
        ("ios", CB_CONNECT_FAST_IOS),
        ("android", CB_CONNECT_FAST_ANDROID),
        ("mac", CB_CONNECT_FAST_MAC),
        ("win", CB_CONNECT_FAST_WIN),
        ("", CB_CONNECT_DEVICE),
    ],
)
def test_connection_nudge_restores_each_platform_fast_path(platform: str, expected_callback: str) -> None:
    _text, keyboard = text_trial_connection_nudge(platform=platform)
    assert keyboard["inline_keyboard"][0][0]["callback_data"] == expected_callback
