"""Holdout уведомлений: детерминированная контрольная группа для nudge-типов,
запись 'holdout' вместо отправки и агрегат эффекта в админке."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.application.notification_holdout import (
    HOLDOUT_ELIGIBLE_TYPES,
    holdout_percent,
    is_notification_holdout,
    notification_holdout_enabled,
)
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.admin_stats import _notification_holdout_stats


def test_flag_and_percent_defaults(monkeypatch) -> None:
    monkeypatch.delenv("NOTIFICATION_HOLDOUT_ENABLE", raising=False)
    assert not notification_holdout_enabled()
    assert holdout_percent() == 10
    monkeypatch.setenv("NOTIFICATION_HOLDOUT_PERCENT", "3")
    assert holdout_percent() == 3
    monkeypatch.setenv("NOTIFICATION_HOLDOUT_PERCENT", "500")
    assert holdout_percent() == 50  # кламп сверху
    monkeypatch.setenv("NOTIFICATION_HOLDOUT_PERCENT", "junk")
    assert holdout_percent() == 10


def test_holdout_disabled_or_ineligible_type_is_false(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFICATION_HOLDOUT_ENABLE", "1")
    # Операционные/информационные типы не удерживаются никогда.
    for notification_type in ("keys_deleted", "trial_expired", "subscription_expired", "unknown_type"):
        assert not is_notification_holdout("user-1", notification_type)
    monkeypatch.setenv("NOTIFICATION_HOLDOUT_ENABLE", "0")
    for notification_type in HOLDOUT_ELIGIBLE_TYPES:
        assert not is_notification_holdout("user-1", notification_type)


def test_holdout_is_deterministic_and_splits(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFICATION_HOLDOUT_ENABLE", "1")
    monkeypatch.setenv("NOTIFICATION_HOLDOUT_PERCENT", "50")
    notification_type = "trial_expiring_24h"
    decisions = {user: is_notification_holdout(user, notification_type) for user in (f"user-{i}" for i in range(200))}
    # Стабильность
    assert decisions["user-7"] == is_notification_holdout("user-7", notification_type)
    # Разбиение ~50%: допускаем ±15 п.п.
    held = sum(decisions.values())
    assert 85 <= held <= 115
    # Один и тот же пользователь удерживается не от всех типов сразу (типы независимы)
    per_user_variety = any(
        is_notification_holdout("user-3", other) != decisions.get("user-3")
        for other in HOLDOUT_ELIGIBLE_TYPES
        if other != notification_type
    )
    assert per_user_variety


def test_scheduler_holdout_writes_status_and_skips_send(monkeypatch) -> None:
    """Holdout-бакет: запись 'holdout' в notification_log, ни один канал не
    трогается (http-клиент не создаётся). Сбой вставки — обычная отправка."""
    from app.runtime.notification_scheduler import NotificationScheduler

    monkeypatch.setenv("NOTIFICATION_HOLDOUT_ENABLE", "1")
    monkeypatch.setattr(
        "app.application.notification_holdout.is_notification_holdout",
        lambda user, notification_type: True,
    )

    pool = AsyncMock()
    pool.fetchrow.return_value = {"telegram_user_id": 42, "has_email": True}
    scheduler = NotificationScheduler(pool=pool, bot_token="test-token", vless_provider=object())

    get_client = AsyncMock()
    monkeypatch.setattr(scheduler, "_get_http_client", get_client)

    async def _run() -> bool:
        return await scheduler._send_notification(
            internal_user_id="user-h",
            notification_type="trial_expiring_24h",
            text="text",
            keyboard={"inline_keyboard": []},
        )

    from app.shared.test_helpers import run_async as _run_helper

    assert _run_helper(_run()) is False
    holdout_inserts = [c for c in pool.execute.await_args_list if "'holdout'" in c.args[0]]
    assert len(holdout_inserts) == 1
    assert "notification_log" in holdout_inserts[0].args[0]
    # Ни Telegram, ни web: http-клиент не запрашивался, второго execute (дедуп) нет.
    get_client.assert_not_called()
    assert len(pool.execute.await_args_list) == 1

    # Fail-open: сбой вставки holdout → отправка продолжается обычным путём.
    # Без email-канала (has_email=False) и с успешным TG-ответом; финальная
    # запись дедупа тоже падает тем же моком — важно лишь, что holdout-ветка
    # не прервала отправку (http-клиент запрошен).
    pool.fetchrow.return_value = {"telegram_user_id": 42, "has_email": False}
    pool.execute.side_effect = RuntimeError("db down")
    pool.fetchval.return_value = None  # нет дедуп-записи
    get_client2 = AsyncMock()
    get_client2.return_value.post = AsyncMock(return_value=AsyncMock(status_code=200))
    monkeypatch.setattr(scheduler, "_get_http_client", get_client2)
    try:
        _run_helper(_run())
    except RuntimeError:
        pass  # финальный дедуп-INSERT падает тем же моком — ожидаемо
    get_client2.assert_awaited()


async def test_postgres_holdout_stats_and_status_constraint(pg_url: str) -> None:
    """CR использует только зрелые окна и не считает оплату до sent_at."""
    import asyncpg

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    u_sent = "test-holdout-sent"
    u_held = "test-holdout-held"
    u_pending = "test-holdout-pending"
    now = datetime.now(UTC)
    mature_at = now - timedelta(days=8)
    pending_at = now - timedelta(days=1)
    try:
        await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
        for uid in (u_sent, u_held, u_pending):
            await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", uid)
            await pool.execute(
                "INSERT INTO user_identities (internal_user_id, telegram_user_id) "
                "VALUES ($1, (9988770000 + abs(hashtext($1)) % 100000)::bigint)",
                uid,
            )
        await pool.execute(
            "INSERT INTO notification_log "
            "(internal_user_id, notification_type, delivery_status, sent_at, sent_date) "
            "VALUES ($1, 'trial_expiring_24h', 'delivered', $2, $3)",
            u_sent,
            mature_at,
            mature_at.date(),
        )
        await pool.execute(
            "INSERT INTO notification_log "
            "(internal_user_id, notification_type, delivery_status, sent_at, sent_date) "
            "VALUES ($1, 'trial_expiring_24h', 'holdout', $2, $3)",
            u_held,
            mature_at,
            mature_at.date(),
        )
        await pool.execute(
            "INSERT INTO notification_log "
            "(internal_user_id, notification_type, delivery_status, sent_at, sent_date) "
            "VALUES ($1, 'trial_expiring_24h', 'delivered', $2, $3)",
            u_pending,
            pending_at,
            pending_at.date(),
        )
        # Только первая оплата входит в CR: у holdout она произошла до exposure,
        # у pending — после exposure, но полное 7-дневное окно ещё не завершено.
        await pool.executemany(
            "INSERT INTO billing_events_ledger "
            "(internal_fact_ref, billing_provider_key, external_event_id, event_type, "
            " event_effective_at, event_received_at, internal_user_id, amount_minor_units, "
            " currency_code, status, ingestion_correlation_id) "
            "VALUES ($1, 'yookassa', $2, 'subscription_activated', "
            "$3, $3, $4, 24900, 'RUB', 'accepted', 'cid-h')",
            [
                ("test-holdout-pay-1", "ext-h-1", mature_at + timedelta(days=1), u_sent),
                ("test-holdout-pay-pre", "ext-h-pre", mature_at - timedelta(days=1), u_held),
                ("test-holdout-pay-pending", "ext-h-pending", pending_at + timedelta(hours=1), u_pending),
            ],
        )

        stats = await _notification_holdout_stats(pool, now - timedelta(days=30), now)
        trial_expiring = stats["trial_expiring_24h"]
        assert trial_expiring["delivered"]["users"] == 1
        assert trial_expiring["delivered"]["total_users"] == 2
        assert trial_expiring["delivered"]["pending_users"] == 1
        assert trial_expiring["delivered"]["paid_users"] == 1
        assert trial_expiring["delivered"]["paid_rate"] == 1.0
        assert trial_expiring["holdout"]["users"] == 1
        assert trial_expiring["holdout"]["total_users"] == 1
        assert trial_expiring["holdout"]["pending_users"] == 0
        assert trial_expiring["holdout"]["paid_users"] == 0
        assert trial_expiring["holdout"]["paid_rate"] == 0.0
    finally:
        await pool.execute("DELETE FROM billing_events_ledger WHERE internal_fact_ref LIKE 'test-holdout-pay%'")
        await pool.execute(
            "DELETE FROM notification_log WHERE internal_user_id = ANY($1)",
            [u_sent, u_held, u_pending],
        )
        await pool.execute(
            "DELETE FROM user_identities WHERE internal_user_id = ANY($1)",
            [u_sent, u_held, u_pending],
        )
        await pool.close()


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping notification holdout integration")
    return url
