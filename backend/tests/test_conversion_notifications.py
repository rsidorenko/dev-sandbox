"""Контракты SQL и идемпотентности конверсионных уведомлений."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest

import app.runtime.notification_scheduler as scheduler_module
from app.bot_transport.storefront_ui import CB_BUY_VPN, CB_CONNECT_FAST_ANDROID, CB_TRIAL
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.runtime.notification_scheduler import NotificationScheduler, _format_msk_datetime

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


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


def test_activation_nudge_targets_recent_non_internal_users_once() -> None:
    pool = _Pool([{"internal_user_id": "u1"}])
    scheduler = _scheduler(pool)

    _run(scheduler._check_trial_activation_nudge(datetime.now(UTC)))

    sql, args = pool.fetch_calls[0]
    assert "u.is_internal = FALSE" in sql
    assert "u.trial_used = FALSE" in sql
    assert "u.created_at <= NOW() - make_interval(mins => $1)" in sql
    assert "s.trial_started_at IS NULL" in sql
    assert "s.plan_id IS NULL" in sql
    assert "NOT EXISTS" in sql
    assert args == (15, 48, "trial_activation_nudge_15m")
    sent = scheduler._send_notification.await_args.kwargs
    assert sent["notification_type"] == "trial_activation_nudge_15m"
    assert sent["keyboard"]["inline_keyboard"][0][0]["callback_data"] == CB_TRIAL


def test_connection_nudge_requires_no_traffic_and_keeps_platform_fast_path() -> None:
    pool = _Pool([{"internal_user_id": "u2", "platform": "android"}])
    scheduler = _scheduler(pool)

    _run(scheduler._check_trial_connection_nudge(datetime.now(UTC)))

    sql, args = pool.fetch_calls[0]
    assert "bare_xray_traffic_daily" in sql
    assert "t.uplink_bytes + t.downlink_bytes > 0" in sql
    assert "ORDER BY e.occurred_at DESC LIMIT 1" in sql
    assert "u.is_internal = FALSE" in sql
    assert args == (15, "trial_connection_nudge_15m")
    sent = scheduler._send_notification.await_args.kwargs
    assert sent["notification_type"] == "trial_connection_nudge_15m"
    assert sent["keyboard"]["inline_keyboard"][0][0]["callback_data"] == CB_CONNECT_FAST_ANDROID


def test_checkout_nudge_is_bounded_and_only_for_unpaid_trial() -> None:
    pool = _Pool([{"internal_user_id": "u3"}])
    scheduler = _scheduler(pool)

    _run(scheduler._check_checkout_abandoned(datetime.now(UTC)))

    sql, args = pool.fetch_calls[0]
    assert "e.event_name = 'checkout_opened'" in sql
    assert "checkout.opened_at <= NOW() - make_interval(mins => $1)" in sql
    assert "checkout.opened_at > NOW() - make_interval(hours => $2)" in sql
    assert "s.plan_id IS NULL" in sql
    assert args == (30, 24, "checkout_abandoned_30m")
    sent = scheduler._send_notification.await_args.kwargs
    assert sent["notification_type"] == "checkout_abandoned_30m"
    assert sent["keyboard"]["inline_keyboard"][0][0]["callback_data"] == CB_BUY_VPN


def test_subscription_warning_deduplicates_current_expiry_window() -> None:
    pool = _Pool()
    scheduler = _scheduler(pool)

    _run(scheduler._check_subscription_expiring(datetime.now(UTC)))

    sql, args = pool.fetch_calls[0]
    assert args == ()
    assert "n.notification_type = 'subscription_expiring_3d'" in sql
    assert "n.sent_at >= s.active_until_utc - INTERVAL '3 days'" in sql
    assert "n.sent_at < s.active_until_utc" in sql


def test_paid_referral_offer_is_suppressed_after_any_referral_offer() -> None:
    pool = _Pool()
    scheduler = _scheduler(pool)

    _run(scheduler._check_referral_offer_paid(datetime.now(UTC)))

    sql, args = pool.fetch_calls[0]
    assert "nl.notification_type = ANY($2::text[])" in sql
    assert args == (25, ["referral_offer_trial", "referral_offer_paid"])


def test_trial_referral_offer_requires_positive_traffic() -> None:
    pool = _Pool()
    scheduler = _scheduler(pool)

    _run(scheduler._check_referral_offer_trial(datetime.now(UTC)))

    sql, args = pool.fetch_calls[0]
    assert args == ()
    assert "bare_xray_traffic_daily" in sql
    assert "t.uplink_bytes + t.downlink_bytes > 0" in sql
    assert "t.stat_date >= (s.trial_started_at AT TIME ZONE 'Europe/Moscow')::date" in sql


def test_scheduler_uses_configured_interval() -> None:
    pool = _Pool()
    scheduler = _scheduler(pool)
    scheduler._run_checks = AsyncMock(side_effect=scheduler.stop)
    scheduler._sleep = AsyncMock()

    _run(scheduler.run())

    scheduler._sleep.assert_awaited_once_with(scheduler_module._CHECK_INTERVAL_SECONDS)


def test_deadline_is_formatted_in_moscow_time() -> None:
    assert _format_msk_datetime(datetime(2026, 9, 14, 9, 30, tzinfo=UTC)) == "14.09 в 12:30"


def test_conversion_queries_execute_on_postgres() -> None:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("DATABASE_URL not set; skipping conversion notification integration test")

    async def _test() -> None:
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
        users = {
            "activation": "conv-activation-test-user",
            "connection": "conv-connection-test-user",
            "checkout": "conv-checkout-test-user",
            "expiring": "conv-expiring-test-user",
            "paid": "conv-paid-test-user",
            "referral": "conv-referral-test-user",
        }
        user_ids = list(users.values())
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS)
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM bare_xray_traffic_daily WHERE server_id = 99201")
                await conn.execute("DELETE FROM funnel_interactions WHERE internal_user_id = ANY($1::text[])", user_ids)
                await conn.execute("DELETE FROM notification_log WHERE internal_user_id = ANY($1::text[])", user_ids)
                await conn.execute(
                    "DELETE FROM subscription_snapshots WHERE internal_user_id = ANY($1::text[])", user_ids
                )
                await conn.execute("DELETE FROM user_identities WHERE internal_user_id = ANY($1::text[])", user_ids)
                for index, uid in enumerate(users.values(), start=1):
                    await conn.execute(
                        "INSERT INTO user_identities "
                        "(telegram_user_id, internal_user_id, trial_used, created_at) "
                        "VALUES ($1, $2, $3, NOW() - INTERVAL '1 hour')",
                        9_920_000_000 + index,
                        uid,
                        uid != users["activation"],
                    )
                for key in ("connection", "checkout", "referral"):
                    await conn.execute(
                        "INSERT INTO subscription_snapshots "
                        "(internal_user_id, state_label, plan_id, trial_started_at, "
                        "trial_expires_at, active_until_utc, updated_at) "
                        "VALUES ($1, 'active', NULL, NOW() - INTERVAL '1 hour', "
                        "NOW() + INTERVAL '2 days', NOW() + INTERVAL '2 days', NOW())",
                        users[key],
                    )
                await conn.execute(
                    "INSERT INTO subscription_snapshots "
                    "(internal_user_id, state_label, plan_id, trial_started_at, "
                    "trial_expires_at, active_until_utc, updated_at) "
                    "VALUES ($1, 'active', NULL, NOW() - INTERVAL '3 days', "
                    "NOW() + INTERVAL '12 hours', NOW() + INTERVAL '12 hours', NOW())",
                    users["expiring"],
                )
                await conn.execute(
                    "INSERT INTO subscription_snapshots "
                    "(internal_user_id, state_label, plan_id, active_until_utc, updated_at) "
                    "VALUES ($1, 'active', '1m', NOW() + INTERVAL '60 hours', NOW())",
                    users["paid"],
                )
                await conn.execute(
                    "INSERT INTO funnel_interactions "
                    "(internal_user_id, event_name, surface, platform, dedup_key, occurred_at) "
                    "VALUES ($1, 'platform_selected', 'telegram', 'android', $2, NOW() - INTERVAL '45 minutes')",
                    users["connection"],
                    "conv-notify:platform",
                )
                await conn.execute(
                    "INSERT INTO funnel_interactions "
                    "(internal_user_id, event_name, surface, platform, dedup_key, occurred_at) "
                    "VALUES ($1, 'checkout_opened', 'telegram', '', $2, NOW() - INTERVAL '45 minutes')",
                    users["checkout"],
                    "conv-notify:checkout",
                )
                await conn.execute(
                    "INSERT INTO bare_xray_traffic_daily "
                    "(stat_date, server_id, email, uplink_bytes, downlink_bytes) "
                    "VALUES ((NOW() AT TIME ZONE 'Europe/Moscow')::date, 99201, $1, 10, 20)",
                    f"user-{users['referral'][:16]}",
                )

            scheduler = NotificationScheduler(pool=pool, bot_token="test", vless_provider=AsyncMock())
            scheduler._send_notification = AsyncMock(return_value=True)

            checks = (
                (scheduler._check_trial_activation_nudge, users["activation"]),
                (scheduler._check_trial_connection_nudge, users["connection"]),
                (scheduler._check_checkout_abandoned, users["checkout"]),
                (scheduler._check_trial_expiring, users["expiring"]),
                (scheduler._check_subscription_expiring, users["paid"]),
                (scheduler._check_referral_offer_trial, users["referral"]),
            )
            for check, expected_user in checks:
                scheduler._send_notification.reset_mock()
                await check(datetime.now(UTC))
                sent_users = {call.kwargs["internal_user_id"] for call in scheduler._send_notification.await_args_list}
                assert expected_user in sent_users
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM bare_xray_traffic_daily WHERE server_id = 99201")
                await conn.execute("DELETE FROM funnel_interactions WHERE internal_user_id = ANY($1::text[])", user_ids)
                await conn.execute("DELETE FROM notification_log WHERE internal_user_id = ANY($1::text[])", user_ids)
                await conn.execute(
                    "DELETE FROM subscription_snapshots WHERE internal_user_id = ANY($1::text[])", user_ids
                )
                await conn.execute("DELETE FROM user_identities WHERE internal_user_id = ANY($1::text[])", user_ids)
            await pool.close()

    asyncio.run(_test())
