"""A/B гейта подписки на канал (trial_channel_gate_v1): резолвер вариантов,
soft-пропуск в боте, идемпотентность назначения (PostgreSQL opt-in)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest

import app.application.trial_gate_experiment as trial_gate_experiment_module
from app.application.trial_gate_experiment import (
    CONTROL_VARIANT,
    SOFT_VARIANT,
    experiment_channel_gate_enabled,
    resolve_trial_gate_variant,
)
from app.bot_transport.runtime_facade import (
    CB_TRIAL,
    handle_slice1_telegram_update_to_rendered_message,
)
from app.bot_transport.storefront_ui import trial_connect_fastpath_keyboard
from app.domain.experiments import TRIAL_CHANNEL_GATE_EXPERIMENT, assign_experiment_variant
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run
from tests.test_bot_transport_runtime_facade import (
    _base_message,
    _callback_update,
    _gate_composition,
    _update,
)


def test_experiment_flag_off_by_default() -> None:
    assert not experiment_channel_gate_enabled()


def test_resolver_returns_control_when_disabled_or_no_pool(monkeypatch) -> None:
    pool = AsyncMock()
    monkeypatch.delenv("EXPERIMENT_CHANNEL_GATE_ENABLE", raising=False)
    assert asyncio.run(resolve_trial_gate_variant(pool, "user-1")) == CONTROL_VARIANT
    monkeypatch.setenv("EXPERIMENT_CHANNEL_GATE_ENABLE", "1")
    assert asyncio.run(resolve_trial_gate_variant(None, "user-1")) == CONTROL_VARIANT
    assert asyncio.run(resolve_trial_gate_variant(pool, None)) == CONTROL_VARIANT
    pool.fetchval.assert_not_called()


def test_resolver_assigns_and_persisted_idempotently(monkeypatch) -> None:
    monkeypatch.setenv("EXPERIMENT_CHANNEL_GATE_ENABLE", "1")
    pool = AsyncMock()
    expected = assign_experiment_variant(internal_user_id="user-1", experiment_key=TRIAL_CHANNEL_GATE_EXPERIMENT)
    pool.fetchval.return_value = expected
    variant = asyncio.run(resolve_trial_gate_variant(pool, "user-1"))
    assert variant in (CONTROL_VARIANT, SOFT_VARIANT)
    sql, *args = pool.fetchval.await_args.args
    assert "INSERT INTO experiment_assignments" in sql
    assert "ON CONFLICT (internal_user_id, experiment_key) DO NOTHING" in sql
    assert args == ["user-1", TRIAL_CHANNEL_GATE_EXPERIMENT, variant]
    # Детерминизм резолвера домена
    assert assign_experiment_variant(internal_user_id="user-1", experiment_key=TRIAL_CHANNEL_GATE_EXPERIMENT) == variant


def test_resolver_fails_open_on_db_error(monkeypatch) -> None:
    monkeypatch.setenv("EXPERIMENT_CHANNEL_GATE_ENABLE", "1")
    pool = AsyncMock()
    pool.fetchval.side_effect = RuntimeError("database unavailable")
    variant = asyncio.run(resolve_trial_gate_variant(pool, "user-1"))
    assert variant == CONTROL_VARIANT


def test_resolver_returns_already_persisted_variant(monkeypatch) -> None:
    monkeypatch.setenv("EXPERIMENT_CHANNEL_GATE_ENABLE", "1")
    pool = AsyncMock()
    pool.fetchval.return_value = SOFT_VARIANT
    assert asyncio.run(resolve_trial_gate_variant(pool, "user-1")) == SOFT_VARIANT


def test_trial_fastpath_keyboard_channel_suggest(monkeypatch) -> None:
    monkeypatch.setenv("TRIAL_CHANNEL_USERNAME", "bravada_vpn")
    plain = trial_connect_fastpath_keyboard()
    suggested = trial_connect_fastpath_keyboard(channel_suggest=True)
    urls_plain = [b.get("url") for row in plain["inline_keyboard"] for b in row if "url" in b]
    urls_suggested = [b.get("url") for row in suggested["inline_keyboard"] for b in row if "url" in b]
    assert "https://t.me/bravada_vpn" not in urls_plain
    assert "https://t.me/bravada_vpn" in urls_suggested
    assert len(suggested["inline_keyboard"]) == len(plain["inline_keyboard"]) + 1


@pytest.mark.parametrize("variant", [CONTROL_VARIANT, SOFT_VARIANT])
def test_facade_gate_variant_controls_gate(monkeypatch, variant: str) -> None:
    """soft пропускает проверку канала (гейт не вызывается, триал активируется,
    клавиатура несёт предложение подписки); control — прежний жёсткий гейт.
    Заглушка резолвера: фасад импортирует его в момент вызова из модуля."""

    async def fake_resolve(pool, internal_user_id):
        return variant

    monkeypatch.setattr(trial_gate_experiment_module, "resolve_trial_gate_variant", fake_resolve)

    async def main() -> None:
        from app.bot_transport.runtime_facade import _is_trial_available

        c = _gate_composition(False)  # не подписан — hard gate обязан показать экран
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_TRIAL, update_id=2), c, correlation_id=new_correlation_id()
        )
        gate = c.trial_channel_gate
        assert gate is not None
        if variant == SOFT_VARIANT:
            assert "Пробный период активирован" in pkg.message_text
            assert gate.calls == []  # проверка канала не выполнялась
            assert await _is_trial_available(c, 42) is False
            urls = [
                b.get("url") for row in (pkg.reply_markup or {"inline_keyboard": []})["inline_keyboard"] for b in row
            ]
            assert any(isinstance(u, str) and "t.me/bravada_vpn" in u for u in urls)
        else:
            assert "Пробный период — для подписчиков канала" in pkg.message_text
            assert gate.calls == [42]
            assert await _is_trial_available(c, 42) is True

    _run(main())


# ─── Интеграционные (PostgreSQL, opt-in через DATABASE_URL) ──────────────


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping trial gate experiment integration")
    return url


async def test_postgres_resolver_persists_stable_assignment(pg_url: str) -> None:
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    internal_user_id = "test-gate-exp-user"
    try:
        await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", internal_user_id)
        await pool.execute(
            "INSERT INTO user_identities (internal_user_id, telegram_user_id) VALUES ($1, 9988776652)",
            internal_user_id,
        )
        os.environ["EXPERIMENT_CHANNEL_GATE_ENABLE"] = "1"
        first = await resolve_trial_gate_variant(pool, internal_user_id)
        # Повторный вызов: INSERT DO NOTHING сохраняет первый вариант.
        second = await resolve_trial_gate_variant(pool, internal_user_id)
        assert first == second
        row = await pool.fetchrow(
            "SELECT variant FROM experiment_assignments WHERE internal_user_id=$1 AND experiment_key=$2",
            internal_user_id,
            TRIAL_CHANNEL_GATE_EXPERIMENT,
        )
        assert row is not None and row["variant"] == first
    finally:
        os.environ.pop("EXPERIMENT_CHANNEL_GATE_ENABLE", None)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", internal_user_id)
        await pool.close()


def test_web_trial_soft_variant_skips_channel_gate(monkeypatch) -> None:
    """Web /trial: soft-вариант не проверяет канал (гейт не вызывается, флоу
    идёт дальше — до lookup identity); control сохраняет жёсткий гейт."""
    from starlette.applications import Starlette
    from starlette.requests import Request

    from app.web_api.profile import handle_activate_trial
    from tests.test_web_channel_subscription import _FakeGate

    class _FakePool:
        def __init__(self) -> None:
            self.execute_calls = 0

        async def execute(self, *args, **kwargs):
            self.execute_calls += 1
            return "INSERT 0 1"

        async def fetchrow(self, *args, **kwargs):
            return None  # identity не найден → предсказуемый 404 после гейта

    async def _auth(_request: Request) -> dict[str, int | str]:
        return {"telegram_user_id": 42, "internal_user_id": "u42"}

    monkeypatch.setattr("app.web_api.profile.require_auth", _auth)

    def _build_request(gate: _FakeGate, pool: _FakePool) -> Request:
        app = Starlette()
        app.state.channel_subscription_required = True
        app.state.channel_membership_checker = gate
        app.state.pool = pool
        return Request(
            {"type": "http", "method": "POST", "path": "/api/v1/user/trial/activate", "headers": [], "app": app}
        )

    for variant, expected_status, expected_gate_calls in ((SOFT_VARIANT, 404, []), (CONTROL_VARIANT, 403, [42])):

        async def _fake_resolve(pool, internal_user_id, resolved=variant):
            return resolved

        monkeypatch.setattr(trial_gate_experiment_module, "resolve_trial_gate_variant", _fake_resolve)
        gate = _FakeGate(False)  # не подписан
        pool = _FakePool()
        result = _run(handle_activate_trial(_build_request(gate, pool)))
        assert result.status_code == expected_status
        assert gate.calls == expected_gate_calls
