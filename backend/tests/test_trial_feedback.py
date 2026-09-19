"""Контракты закрытой обратной связи по триалу."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.application.trial_feedback import record_trial_feedback
from app.bot_transport.storefront_ui import (
    CB_BUY_VPN,
    CB_TRIAL_FEEDBACK_ANSWER,
    connection_feedback_keyboard,
    parse_trial_feedback_answer,
    trial_nonpayment_feedback_keyboard,
)
from app.persistence.postgres_migrations import apply_postgres_migrations

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip("DATABASE_URL not set; skipping trial feedback integration")
    return url


def _run(awaitable):
    return asyncio.run(awaitable)


def _callbacks(keyboard: dict) -> list[str]:
    return [
        button["callback_data"] for row in keyboard["inline_keyboard"] for button in row if "callback_data" in button
    ]


@pytest.mark.parametrize("route_token,route", [("d", "direct"), ("g", "guided")])
def test_connection_feedback_callback_is_closed_and_under_telegram_limit(route_token: str, route: str) -> None:
    keyboard = connection_feedback_keyboard("android", route)
    callbacks = _callbacks(keyboard)
    assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks)
    assert parse_trial_feedback_answer(f"{CB_TRIAL_FEEDBACK_ANSWER}p:{route_token}:android:traffic_failed") == (
        "connection_problem",
        "traffic_failed",
        "android",
        route,
    )
    assert parse_trial_feedback_answer(f"{CB_TRIAL_FEEDBACK_ANSWER}p:{route_token}:android:skip") == (
        "connection_problem",
        "skip",
        "android",
        route,
    )


def test_nonpayment_feedback_callback_and_skip_are_safe() -> None:
    callbacks = _callbacks(trial_nonpayment_feedback_keyboard())
    assert CB_BUY_VPN in callbacks
    assert parse_trial_feedback_answer(f"{CB_TRIAL_FEEDBACK_ANSWER}n:too_expensive") == (
        "trial_nonpayment",
        "too_expensive",
        "",
        "",
    )
    assert parse_trial_feedback_answer(f"{CB_TRIAL_FEEDBACK_ANSWER}n:secret") is None


def test_recorder_rejects_unknown_context_before_database() -> None:
    pool = AsyncMock()
    assert not _run(
        record_trial_feedback(
            pool,
            feedback_type="connection_problem",
            reason_code="secret",
            telegram_user_id=42,
            platform="ios",
            connection_route="direct",
        )
    )
    assert not _run(
        record_trial_feedback(
            pool,
            feedback_type="trial_nonpayment",
            reason_code="too_expensive",
            telegram_user_id=42,
            platform="ios",
        )
    )
    pool.execute.assert_not_called()


def test_recorder_uses_idempotent_upsert_and_fails_open() -> None:
    pool = AsyncMock()
    assert _run(
        record_trial_feedback(
            pool,
            feedback_type="connection_problem",
            reason_code="connect_failed",
            telegram_user_id=42,
            platform="ios",
            connection_route="guided",
        )
    )
    sql, *args = pool.execute.await_args.args
    assert "ON CONFLICT (internal_user_id, feedback_type, platform, surface) DO UPDATE" in sql
    assert args == ["connection_problem", "connect_failed", "ios", "guided", "telegram", None, 42]

    pool.execute.side_effect = RuntimeError("database unavailable")
    assert not _run(
        record_trial_feedback(
            pool,
            feedback_type="trial_nonpayment",
            reason_code="no_need",
            telegram_user_id=42,
        )
    )


async def test_postgres_recorder_updates_one_current_answer(pg_url: str) -> None:
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    internal_user_id = "test-trial-feedback-user"
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS)
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", internal_user_id)
        await pool.execute(
            "INSERT INTO user_identities (internal_user_id,telegram_user_id) VALUES ($1,$2)",
            internal_user_id,
            9_988_776_655,
        )
        assert await record_trial_feedback(
            pool,
            feedback_type="connection_problem",
            reason_code="connect_failed",
            internal_user_id=internal_user_id,
            platform="ios",
            connection_route="direct",
        )
        assert await record_trial_feedback(
            pool,
            feedback_type="connection_problem",
            reason_code="traffic_failed",
            internal_user_id=internal_user_id,
            platform="ios",
            connection_route="guided",
        )
        assert await record_trial_feedback(
            pool,
            feedback_type="connection_problem",
            reason_code="import_failed",
            internal_user_id=internal_user_id,
            platform="ios",
            connection_route="",
            surface="web",
        )
        rows = await pool.fetch(
            "SELECT reason_code,connection_route,surface FROM trial_feedback "
            "WHERE internal_user_id=$1 ORDER BY surface",
            internal_user_id,
        )
        assert [dict(row) for row in rows] == [
            {"reason_code": "traffic_failed", "connection_route": "guided", "surface": "telegram"},
            {"reason_code": "import_failed", "connection_route": "", "surface": "web"},
        ]
    finally:
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", internal_user_id)


async def test_postgres_accepts_web_route_empty_connection_problem(pg_url: str) -> None:
    """Web-страница /check шлёт connection_problem с route='' (и platform='' при
    неопознанном UA) — констрейнт 094 обязан такие принимать, иначе причины
    молча теряются (recorder fail-open)."""
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    internal_user_id = "test-trial-feedback-web"
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS)
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", internal_user_id)
        await pool.execute(
            "INSERT INTO user_identities (internal_user_id,telegram_user_id) VALUES ($1,$2)",
            internal_user_id,
            9_988_776_654,
        )
        assert await record_trial_feedback(
            pool,
            feedback_type="connection_problem",
            reason_code="connect_failed",
            internal_user_id=internal_user_id,
            platform="",
            connection_route="",
            surface="web",
        )
        row = await pool.fetchrow(
            "SELECT reason_code,surface FROM trial_feedback "
            "WHERE internal_user_id=$1 AND feedback_type='connection_problem'",
            internal_user_id,
        )
        assert row is not None and row["reason_code"] == "connect_failed" and row["surface"] == "web"
    finally:
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", internal_user_id)
        await pool.close()
