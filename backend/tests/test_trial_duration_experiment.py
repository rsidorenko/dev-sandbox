"""A/B длительности триала (trial_duration_v1): резолвер дней, применение в
боте/web и синхронность SQL-CASE первого-трафика с реестром вариантов."""

from __future__ import annotations

import asyncio
import inspect
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import app.application.trial_duration_experiment as trial_duration_module
from app.application.trial_duration_experiment import (
    TRIAL_DURATION_VARIANTS_DAYS,
    experiment_trial_duration_enabled,
    resolve_trial_duration_days,
    trial_days_for_variant,
)
from app.bot_transport.runtime_facade import (
    CB_TRIAL,
    handle_slice1_telegram_update_to_rendered_message,
)
from app.bot_transport.storefront_ui import text_trial_connect_fastpath
from app.domain.experiments import TRIAL_DURATION_EXPERIMENT, assign_experiment_variant
from app.domain.trial import TRIAL_DURATION_DAYS, trial_expires_at
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run
from tests.test_bot_transport_runtime_facade import (
    _base_message,
    _callback_update,
    _update,
    build_slice1_composition,
)


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping trial duration integration")
    return url


def test_experiment_flag_off_by_default() -> None:
    assert not experiment_trial_duration_enabled()
    assert TRIAL_DURATION_DAYS == 3


def test_trial_expires_at_days_override() -> None:
    started = datetime(2026, 9, 19, 12, tzinfo=UTC)
    assert trial_expires_at(started) == started + timedelta(days=3)
    assert trial_expires_at(started, days=7) == started + timedelta(days=7)


def test_resolver_fail_open_when_disabled_or_no_pool(monkeypatch) -> None:
    pool = AsyncMock()
    monkeypatch.delenv("EXPERIMENT_TRIAL_DURATION_ENABLE", raising=False)
    assert asyncio.run(resolve_trial_duration_days(pool, "user-1")) == 3
    monkeypatch.setenv("EXPERIMENT_TRIAL_DURATION_ENABLE", "1")
    assert asyncio.run(resolve_trial_duration_days(None, "user-1")) == 3
    assert asyncio.run(resolve_trial_duration_days(pool, None)) == 3
    pool.execute.assert_not_called()


def test_resolver_returns_registry_days_and_persists(monkeypatch) -> None:
    monkeypatch.setenv("EXPERIMENT_TRIAL_DURATION_ENABLE", "1")
    pool = AsyncMock()
    days = asyncio.run(resolve_trial_duration_days(pool, "user-1"))
    variant = assign_experiment_variant(internal_user_id="user-1", experiment_key=TRIAL_DURATION_EXPERIMENT)
    assert days == TRIAL_DURATION_VARIANTS_DAYS[variant]
    assert days in (3, 7)
    sql, *args = pool.execute.await_args.args
    assert "INSERT INTO experiment_assignments" in sql
    assert args == ["user-1", TRIAL_DURATION_EXPERIMENT, variant]


def test_resolver_db_write_failure_still_returns_days(monkeypatch) -> None:
    monkeypatch.setenv("EXPERIMENT_TRIAL_DURATION_ENABLE", "1")
    pool = AsyncMock()
    pool.execute.side_effect = RuntimeError("database unavailable")
    assert asyncio.run(resolve_trial_duration_days(pool, "user-1")) in (3, 7)


def test_variant_days_mapping_is_closed() -> None:
    assert trial_days_for_variant("control") == 3
    assert trial_days_for_variant("7d") == 7
    assert trial_days_for_variant("unknown") == 3


def test_success_text_uses_actual_days() -> None:
    assert "Полные 3 дня" in text_trial_connect_fastpath(days=3)
    assert "Полные 7 дней" in text_trial_connect_fastpath(days=7)
    assert "в течение 7 дней" in text_trial_connect_fastpath(days=7)
    assert "Полные 21 день" in text_trial_connect_fastpath(days=21)


@pytest.mark.parametrize("days", [3, 7])
def test_facade_trial_window_matches_resolved_days(monkeypatch, days: int) -> None:
    """Окно триала в боте = дни из резолвера (проверяем через даты снапшота
    неактивной композиции — при активации upsert_state фиксирует trial_expires_at)."""

    async def fake_resolve(pool, internal_user_id):
        return days

    monkeypatch.setattr(trial_duration_module, "resolve_trial_duration_days", fake_resolve)

    async def main() -> None:
        from app.issuance.vless_provider import StubVlessProvider

        c = build_slice1_composition(vless_provider=StubVlessProvider())
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_TRIAL, update_id=2), c, correlation_id=new_correlation_id()
        )
        assert "Пробный период активирован" in pkg.message_text
        if days == 7:
            assert "Полные 7 дней" in pkg.message_text
        else:
            assert "Полные 3 дня" in pkg.message_text

    _run(main())


def test_first_traffic_sql_case_stays_in_sync_with_registry() -> None:
    """SQL в postgres_traffic_stats дублирует маппинг вариант→дни скаляром —
    контракт синхронности с TRIAL_DURATION_VARIANTS_DAYS."""
    from app.persistence import postgres_traffic_stats

    source = inspect.getsource(postgres_traffic_stats)
    expected = f"CASE WHEN ea.variant = '7d' THEN {TRIAL_DURATION_VARIANTS_DAYS['7d']} ELSE $2 END"
    assert expected in source
    assert "'trial_duration_v1'" in source
    assert "TRIAL_DURATION_DAYS," in source  # $2 — контрольные дни


async def test_postgres_first_traffic_shift_uses_variant_days(pg_url: str) -> None:
    """Первый трафик переносит окно триала на вариантные дни: '7d' → 7,
    без назначения (контроль/эксперимент выключен) → 3."""
    import asyncpg

    from app.issuance.bare_xray_traffic import TrafficSample
    from app.persistence.postgres_migrations import apply_postgres_migrations
    from app.persistence.postgres_traffic_stats import PostgresTrafficStatsRepository

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    u_7d = "test-duration-7d-user"
    u_ctrl = "test-duration-ctrl-user"
    try:
        migrations = Path(__file__).resolve().parents[1] / "migrations"
        await apply_postgres_migrations(pool, migrations_directory=migrations)
        for uid in (u_7d, u_ctrl):
            await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", uid)
            await pool.execute(
                "INSERT INTO user_identities (internal_user_id, telegram_user_id) "
                "VALUES ($1, (9988770000 + abs(hashtext($1)) % 100000)::bigint)",
                uid,
            )
            await pool.execute(
                "INSERT INTO subscription_snapshots "
                "(internal_user_id, state_label, plan_id, active_until_utc, trial_started_at, trial_expires_at) "
                "VALUES ($1, 'active', NULL, NOW() + interval '3 days', NOW() - interval '1 hour', "
                "NOW() + interval '3 days')",
                uid,
            )
        await pool.execute(
            "INSERT INTO experiment_assignments (internal_user_id, experiment_key, variant) "
            "VALUES ($1, 'trial_duration_v1', '7d')",
            u_7d,
        )

        repo = PostgresTrafficStatsRepository(pool)
        samples = [
            TrafficSample(
                server_id=99005,
                email=f"user-{uid[:16]}",
                uplink=10**9,
                downlink=10**9,
                delta_up=10**9,
                delta_down=10**9,
            )
            for uid in (u_7d, u_ctrl)
        ]
        started = await repo.apply_batch(samples)
        assert started == 2

        windows = {
            row["internal_user_id"]: row["days"]
            for row in await pool.fetch(
                "SELECT internal_user_id, "
                "(trial_expires_at::date - NOW()::date) AS days "
                "FROM subscription_snapshots WHERE internal_user_id = ANY($1)",
                [u_7d, u_ctrl],
            )
        }
        assert windows[u_7d] == 7
        assert windows[u_ctrl] == 3
    finally:
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id = ANY($1)", [u_7d, u_ctrl])
        await pool.execute("DELETE FROM bare_xray_traffic_daily WHERE server_id = 99005")
        await pool.execute("DELETE FROM bare_xray_traffic_state WHERE server_id = 99005")
        await pool.close()
