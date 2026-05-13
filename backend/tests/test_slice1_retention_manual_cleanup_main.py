"""Unit tests for manual slice-1 retention cleanup CLI entrypoint (no real database)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.persistence import slice1_retention_manual_cleanup_main as main_mod
from app.persistence.slice1_retention_manual_cleanup import (
    ENV_BATCH,
    ENV_DRY_RUN,
    ENV_MAX_ROUNDS,
    ENV_TTL,
    RetentionCleanupResult,
    RetentionSettings,
)
from app.security.config import ConfigurationError, RuntimeConfig
from tests.retention_boundary_assertions import assert_retention_failure_output_safe

_SYNTHETIC_DSN_SECRET = "TOP_SECRET_XYZabc123"
_SYNTHETIC_DSN = f"postgresql://user:{_SYNTHETIC_DSN_SECRET}@127.0.0.1:5432/slice1_retention_testdb"


def _valid_retention_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TTL, "3600")
    monkeypatch.setenv(ENV_BATCH, "100")
    monkeypatch.setenv(ENV_MAX_ROUNDS, "5")
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)


def _classify_retention_boundary_exception_for_tests(exc: BaseException) -> str:
    if isinstance(exc, ConfigurationError):
        return "config_error"
    if isinstance(exc, ConnectionError | OSError):
        return "dependency_error"
    return "unexpected_error"


def test_load_retention_settings_from_env_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TTL, "86400")
    monkeypatch.setenv(ENV_BATCH, "50")
    monkeypatch.setenv(ENV_MAX_ROUNDS, "3")
    monkeypatch.setenv(ENV_DRY_RUN, "0")
    expected = RetentionSettings(
        ttl_seconds=86400,
        batch_limit=50,
        dry_run=False,
        max_rounds=3,
    )
    assert main_mod.load_retention_settings_from_env() == expected


@pytest.mark.parametrize(
    "dry_raw",
    ("1", "true", "yes", " TRUE ", " True ", "YES"),
)
def test_load_retention_settings_from_env_dry_run_truthy_matrix(
    monkeypatch: pytest.MonkeyPatch,
    dry_raw: str,
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.setenv(ENV_DRY_RUN, dry_raw)
    settings = main_mod.load_retention_settings_from_env()
    assert settings.dry_run is True


@pytest.mark.parametrize(
    "dry_raw",
    ("", "0", "false", "False", "no", "random", "  false  "),
)
def test_load_retention_settings_from_env_dry_run_falsey_matrix(
    monkeypatch: pytest.MonkeyPatch,
    dry_raw: str,
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.setenv(ENV_DRY_RUN, dry_raw)
    settings = main_mod.load_retention_settings_from_env()
    assert settings.dry_run is False


@pytest.mark.parametrize(
    ("unset_name", "missing_key"),
    [
        (ENV_TTL, ENV_TTL),
        (ENV_BATCH, ENV_BATCH),
        (ENV_MAX_ROUNDS, ENV_MAX_ROUNDS),
    ],
)
def test_load_retention_settings_from_env_missing_positive_int(
    monkeypatch: pytest.MonkeyPatch,
    unset_name: str,
    missing_key: str,
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.delenv(unset_name, raising=False)
    monkeypatch.setenv("DATABASE_URL", _SYNTHETIC_DSN)
    with pytest.raises(ConfigurationError) as exc:
        main_mod.load_retention_settings_from_env()
    msg = str(exc.value)
    assert missing_key in msg
    assert _SYNTHETIC_DSN_SECRET not in msg
    assert "postgresql://" not in msg


@pytest.mark.parametrize(
    ("env_name", "raw_value"),
    [
        (ENV_TTL, ""),
        (ENV_TTL, "   "),
        (ENV_BATCH, ""),
        (ENV_MAX_ROUNDS, ""),
    ],
)
def test_load_retention_settings_from_env_blank_positive_int(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    raw_value: str,
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.setenv(env_name, raw_value)
    monkeypatch.setenv("DATABASE_URL", _SYNTHETIC_DSN)
    with pytest.raises(ConfigurationError) as exc:
        main_mod.load_retention_settings_from_env()
    msg = str(exc.value)
    assert env_name in msg
    assert _SYNTHETIC_DSN_SECRET not in msg


@pytest.mark.parametrize(
    ("env_name", "raw_value"),
    [
        (ENV_TTL, "x"),
        (ENV_TTL, "1.5"),
        (ENV_BATCH, "nope"),
        (ENV_MAX_ROUNDS, "not-an-int"),
    ],
)
def test_load_retention_settings_from_env_non_integer_positive_int(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    raw_value: str,
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.setenv(env_name, raw_value)
    monkeypatch.setenv("DATABASE_URL", _SYNTHETIC_DSN)
    with pytest.raises(ConfigurationError) as exc:
        main_mod.load_retention_settings_from_env()
    msg = str(exc.value)
    assert env_name in msg
    assert _SYNTHETIC_DSN_SECRET not in msg


@pytest.mark.parametrize(
    ("env_name", "raw_value"),
    [
        (ENV_TTL, "0"),
        (ENV_TTL, "-1"),
        (ENV_BATCH, "0"),
        (ENV_MAX_ROUNDS, "-3"),
    ],
)
def test_load_retention_settings_from_env_non_positive_int(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    raw_value: str,
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.setenv(env_name, raw_value)
    monkeypatch.setenv("DATABASE_URL", _SYNTHETIC_DSN)
    with pytest.raises(ConfigurationError) as exc:
        main_mod.load_retention_settings_from_env()
    msg = str(exc.value)
    assert env_name in msg
    assert _SYNTHETIC_DSN_SECRET not in msg


class _FakePool:
    def __init__(self, conn: object) -> None:
        self.conn = conn
        self.acquire_count = 0
        self.closed = False
        self.close_calls = 0

    def acquire(self) -> object:
        self.acquire_count += 1

        @asynccontextmanager
        async def _cm():
            yield self.conn

        return _cm()

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


@pytest.mark.asyncio
async def test_run_slice1_retention_cleanup_from_env_cleanup_failure_closes_pool_no_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=_SYNTHETIC_DSN,
        app_env="development",
        debug_safe=False,
    )
    settings = RetentionSettings(
        ttl_seconds=3600,
        batch_limit=10,
        dry_run=False,
        max_rounds=2,
    )
    cleanup = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    fake_pool: _FakePool | None = None

    async def fake_open_pool(url: str) -> _FakePool:
        nonlocal fake_pool
        assert url == _SYNTHETIC_DSN
        fake_pool = _FakePool(conn=object())
        return fake_pool

    monkeypatch.setattr(main_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(
        main_mod,
        "load_retention_settings_from_env",
        lambda: settings,
    )
    monkeypatch.setattr(main_mod, "_default_open_pool", fake_open_pool)
    monkeypatch.setattr(main_mod, "run_slice1_retention_cleanup", cleanup)

    with pytest.raises(RuntimeError, match="cleanup failed") as excinfo:
        await main_mod.run_slice1_retention_cleanup_from_env()

    assert fake_pool is not None
    assert fake_pool.acquire_count == 1
    assert fake_pool.close_calls == 1
    cleanup.assert_awaited_once()

    err_text = str(excinfo.value)
    assert _classify_retention_boundary_exception_for_tests(excinfo.value) == "unexpected_error"

    captured = capsys.readouterr()
    assert captured.out == ""
    assert_retention_failure_output_safe(
        err_text,
        captured.err,
        summary_stdout=captured.out,
        summary_stderr=captured.err,
        summary_markers=("slice1_retention_cleanup",),
    )


@pytest.mark.asyncio
async def test_run_slice1_retention_cleanup_from_env_wiring(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dsn = "postgresql://local:test@127.0.0.1:5432/db"
    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=dsn,
        app_env="development",
        debug_safe=False,
    )
    settings = RetentionSettings(
        ttl_seconds=3600,
        batch_limit=10,
        dry_run=True,
        max_rounds=2,
    )
    fake_result = RetentionCleanupResult(
        dry_run=True,
        cutoff_iso="2020-01-01T00:00:00+00:00",
        audit_rows=3,
        idempotency_rows=4,
        outbound_delivery_rows_matched=9,
        outbound_delivery_rows_deleted=0,
        rounds=1,
    )
    cleanup = AsyncMock(return_value=fake_result)
    open_dsns: list[str] = []
    fake_pool: _FakePool | None = None

    async def fake_open_pool(url: str) -> _FakePool:
        nonlocal fake_pool
        open_dsns.append(url)
        fake_pool = _FakePool(conn=object())
        return fake_pool

    monkeypatch.setattr(main_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(
        main_mod,
        "load_retention_settings_from_env",
        lambda: settings,
    )
    monkeypatch.setattr(main_mod, "_default_open_pool", fake_open_pool)
    monkeypatch.setattr(main_mod, "run_slice1_retention_cleanup", cleanup)

    await main_mod.run_slice1_retention_cleanup_from_env()

    assert open_dsns == [dsn]
    assert fake_pool is not None
    assert fake_pool.acquire_count == 1
    assert fake_pool.close_calls == 1
    assert fake_pool.closed is True
    cleanup.assert_awaited_once()
    (_conn,), kwargs = cleanup.call_args
    assert kwargs["settings"] is settings
    assert kwargs["now_utc"].tzinfo is not None

    out = capsys.readouterr().out
    assert "slice1_retention_cleanup" in out
    assert "dry_run=True" in out
    assert "cutoff=2020-01-01T00:00:00+00:00" in out
    assert "audit_rows=3" in out
    assert "idempotency_rows=4" in out
    assert "outbound_delivery_rows_matched=9" in out
    assert "outbound_delivery_rows_deleted=0" in out
    assert "rounds=1" in out
    assert dsn not in out


@pytest.mark.asyncio
async def test_run_slice1_retention_cleanup_from_env_config_failure_no_dsn_leak(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "12345678901")
    monkeypatch.setenv("DATABASE_URL", _SYNTHETIC_DSN)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv(ENV_TTL, raising=False)

    with pytest.raises(ConfigurationError):
        await main_mod.run_slice1_retention_cleanup_from_env()

    captured = capsys.readouterr()
    assert_retention_failure_output_safe(
        captured.out,
        captured.err,
        summary_stdout=captured.out,
        summary_stderr=captured.err,
        summary_markers=("slice1_retention_cleanup",),
    )


@pytest.mark.asyncio
async def test_run_slice1_retention_cleanup_from_env_config_failure_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "12345678901")
    monkeypatch.setenv("DATABASE_URL", _SYNTHETIC_DSN)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv(ENV_TTL, raising=False)

    with pytest.raises(ConfigurationError) as exc_info:
        await main_mod.run_slice1_retention_cleanup_from_env()

    assert _classify_retention_boundary_exception_for_tests(exc_info.value) == "config_error"


@pytest.mark.asyncio
async def test_run_slice1_retention_cleanup_from_env_dependency_failure_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=_SYNTHETIC_DSN,
        app_env="development",
        debug_safe=False,
    )
    settings = RetentionSettings(
        ttl_seconds=3600,
        batch_limit=10,
        dry_run=False,
        max_rounds=2,
    )

    async def fail_open_pool(_url: str) -> _FakePool:
        raise ConnectionError("pool open failed")

    monkeypatch.setattr(main_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(main_mod, "load_retention_settings_from_env", lambda: settings)
    monkeypatch.setattr(main_mod, "_default_open_pool", fail_open_pool)

    with pytest.raises(ConnectionError) as exc_info:
        await main_mod.run_slice1_retention_cleanup_from_env()

    assert _classify_retention_boundary_exception_for_tests(exc_info.value) == "dependency_error"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert_retention_failure_output_safe(
        exc_info.value,
        captured.out,
        captured.err,
        summary_stdout=captured.out,
        summary_stderr=captured.err,
        summary_markers=("slice1_retention_cleanup",),
    )


def test_main_delegates_to_asyncio_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def fake_run(awaitable: object) -> None:
        seen.append(awaitable)

    monkeypatch.setattr(main_mod.asyncio, "run", fake_run)

    main_mod.main()

    assert len(seen) == 1
    seen[0].close()  # coroutine cleanup, same contract as other *_main tests
