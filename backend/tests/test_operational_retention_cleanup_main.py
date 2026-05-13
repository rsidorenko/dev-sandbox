"""Unit tests for operational retention cleanup entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.persistence import operational_retention_cleanup_main as main_mod
from app.persistence.operational_retention_cleanup import (
    ENV_ADM02_AUDIT_RETENTION_DAYS,
    ENV_OPERATIONAL_RETENTION_DELETE_ENABLE,
    OperationalRetentionResult,
)
from app.security.config import ConfigurationError, RuntimeConfig
from tests.retention_boundary_assertions import assert_retention_failure_output_safe


def test_load_settings_defaults_to_dry_run_and_conservative_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_OPERATIONAL_RETENTION_DELETE_ENABLE, raising=False)
    monkeypatch.delenv(ENV_ADM02_AUDIT_RETENTION_DAYS, raising=False)
    settings = main_mod.load_operational_retention_settings_from_env()
    assert settings.dry_run is True
    assert settings.adm02_audit_retention_days == 365


@pytest.mark.parametrize("raw", ("1", "true", "yes", " True "))
def test_load_settings_truthy_delete_opt_in_disables_dry_run(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(ENV_OPERATIONAL_RETENTION_DELETE_ENABLE, raw)
    monkeypatch.setenv(ENV_ADM02_AUDIT_RETENTION_DAYS, "180")
    settings = main_mod.load_operational_retention_settings_from_env()
    assert settings.dry_run is False
    assert settings.adm02_audit_retention_days == 180


@pytest.mark.parametrize("raw", ("", "0", "false", "no", "random"))
def test_load_settings_falsey_delete_opt_in_keeps_dry_run(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(ENV_OPERATIONAL_RETENTION_DELETE_ENABLE, raw)
    settings = main_mod.load_operational_retention_settings_from_env()
    assert settings.dry_run is True


@pytest.mark.parametrize("raw", ("abc", "0", "-1"))
def test_invalid_retention_days_fails_safe(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(ENV_ADM02_AUDIT_RETENTION_DAYS, raw)
    with pytest.raises(ConfigurationError, match=ENV_ADM02_AUDIT_RETENTION_DAYS):
        main_mod.load_operational_retention_settings_from_env()


class _FakePool:
    def __init__(self, conn: object) -> None:
        self.conn = conn
        self.close_calls = 0
        self.acquire_calls = 0

    def acquire(self) -> object:
        self.acquire_calls += 1

        @asynccontextmanager
        async def _cm():
            yield self.conn

        return _cm()

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_main_wiring_prints_safe_counts_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = RuntimeConfig(
        bot_token="1234567890tok",
        database_url="postgresql://user:secret@127.0.0.1:5432/db",
        app_env="development",
        debug_safe=False,
    )
    fake_pool = _FakePool(conn=object())
    cleanup = AsyncMock(
        return_value=OperationalRetentionResult(
            dry_run=True,
            telegram_update_dedup_expired_rows=2,
            telegram_update_dedup_deleted_rows=0,
            adm02_audit_expired_rows=3,
            adm02_audit_deleted_rows=0,
            adm02_audit_retention_days=365,
        )
    )

    async def fake_open_pool(_dsn: str) -> _FakePool:
        return fake_pool

    monkeypatch.setattr(main_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(main_mod, "_default_open_pool", fake_open_pool)
    monkeypatch.setattr(main_mod, "run_operational_retention_cleanup", cleanup)
    monkeypatch.delenv(ENV_OPERATIONAL_RETENTION_DELETE_ENABLE, raising=False)
    monkeypatch.delenv(ENV_ADM02_AUDIT_RETENTION_DAYS, raising=False)

    await main_mod.run_operational_retention_cleanup_from_env()

    out = capsys.readouterr().out
    assert "operational_retention_cleanup" in out
    assert "dry_run=True" in out
    assert "telegram_update_dedup_expired_rows=2" in out
    assert "adm02_audit_expired_rows=3" in out
    assert "DATABASE_URL" not in out
    assert "postgresql://" not in out
    assert fake_pool.acquire_calls == 1
    assert fake_pool.close_calls == 1


@pytest.mark.asyncio
async def test_missing_database_url_fails_without_leaky_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = RuntimeConfig(
        bot_token="1234567890tok",
        database_url=None,
        app_env="development",
        debug_safe=False,
    )
    monkeypatch.setattr(main_mod, "load_runtime_config", lambda: config)

    with pytest.raises(ConfigurationError):
        await main_mod.run_operational_retention_cleanup_from_env()

    captured = capsys.readouterr()
    assert_retention_failure_output_safe(
        captured.out,
        captured.err,
        summary_stdout=captured.out,
        summary_stderr=captured.err,
        summary_markers=("operational_retention_cleanup",),
    )
