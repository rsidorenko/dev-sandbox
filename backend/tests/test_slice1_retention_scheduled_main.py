"""Unit tests for scheduled slice-1 retention entrypoint (no real database)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.persistence import slice1_retention_scheduled_main as scheduled_mod
from app.persistence.slice1_retention_manual_cleanup import (
    ENV_BATCH,
    ENV_MAX_ROUNDS,
    ENV_TTL,
    RetentionCleanupResult,
    RetentionSettings,
)
from app.security.config import ConfigurationError, RuntimeConfig
from tests.retention_boundary_assertions import assert_retention_failure_output_safe

_SYNTHETIC_DSN = "postgresql://user:secret@127.0.0.1:5432/scheduled_test"
_SYNTHETIC_DSN_SECRET = "secret"

ENV_SCHED = scheduled_mod.SLICE1_RETENTION_SCHEDULED_ENABLE_DELETE


def _valid_retention_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TTL, "3600")
    monkeypatch.setenv(ENV_BATCH, "100")
    monkeypatch.setenv(ENV_MAX_ROUNDS, "5")


def _classify_retention_boundary_exception_for_tests(exc: BaseException) -> str:
    if isinstance(exc, ConfigurationError):
        return "config_error"
    if isinstance(exc, ConnectionError | OSError):
        return "dependency_error"
    return "unexpected_error"


def test_scheduled_no_opt_in_forces_dry_run_true(monkeypatch: pytest.MonkeyPatch) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.delenv(ENV_SCHED, raising=False)

    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=_SYNTHETIC_DSN,
        app_env="development",
        debug_safe=False,
    )
    loaded = RetentionSettings(
        ttl_seconds=3600,
        batch_limit=100,
        dry_run=False,
        max_rounds=5,
    )
    cleanup = AsyncMock(
        return_value=RetentionCleanupResult(
            dry_run=True,
            cutoff_iso="c",
            audit_rows=0,
            idempotency_rows=0,
            outbound_delivery_rows_matched=0,
            outbound_delivery_rows_deleted=0,
            rounds=0,
        )
    )

    async def fake_open_pool(_url: str) -> object:
        class P:
            def acquire(self) -> object:
                @asynccontextmanager
                async def _cm():
                    yield object()

                return _cm()

            async def close(self) -> None: ...

        return P()

    monkeypatch.setattr(scheduled_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(scheduled_mod, "load_retention_settings_from_env", lambda: loaded)
    monkeypatch.setattr(scheduled_mod, "_default_open_pool", fake_open_pool)
    monkeypatch.setattr(scheduled_mod, "run_slice1_retention_cleanup", cleanup)

    asyncio.run(scheduled_mod.run_slice1_retention_scheduled_from_env())

    (_conn,), kwargs = cleanup.call_args
    assert kwargs["settings"].dry_run is True


def test_scheduled_no_opt_in_overrides_only_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.delenv(ENV_SCHED, raising=False)

    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=_SYNTHETIC_DSN,
        app_env="development",
        debug_safe=False,
    )
    loaded = RetentionSettings(
        ttl_seconds=7777,
        batch_limit=33,
        dry_run=False,
        max_rounds=9,
    )
    cleanup = AsyncMock(
        return_value=RetentionCleanupResult(
            dry_run=True,
            cutoff_iso="c",
            audit_rows=0,
            idempotency_rows=0,
            outbound_delivery_rows_matched=0,
            outbound_delivery_rows_deleted=0,
            rounds=0,
        )
    )

    async def fake_open_pool(_url: str) -> object:
        class P:
            def acquire(self) -> object:
                @asynccontextmanager
                async def _cm():
                    yield object()

                return _cm()

            async def close(self) -> None: ...

        return P()

    monkeypatch.setattr(scheduled_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(scheduled_mod, "load_retention_settings_from_env", lambda: loaded)
    monkeypatch.setattr(scheduled_mod, "_default_open_pool", fake_open_pool)
    monkeypatch.setattr(scheduled_mod, "run_slice1_retention_cleanup", cleanup)

    asyncio.run(scheduled_mod.run_slice1_retention_scheduled_from_env())

    cleanup.assert_awaited_once()
    (_conn,), kwargs = cleanup.call_args
    assert kwargs["settings"].dry_run is True
    assert kwargs["settings"].ttl_seconds == 7777
    assert kwargs["settings"].batch_limit == 33
    assert kwargs["settings"].max_rounds == 9


@pytest.mark.parametrize("raw", ("", "0", "false", "no", "random"))
def test_scheduled_falsey_matrix_forces_dry_run_true(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    raw: str,
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.setenv(ENV_SCHED, raw)

    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=_SYNTHETIC_DSN,
        app_env="development",
        debug_safe=False,
    )
    loaded = RetentionSettings(
        ttl_seconds=3600,
        batch_limit=100,
        dry_run=False,
        max_rounds=5,
    )
    cleanup = AsyncMock(
        return_value=RetentionCleanupResult(
            dry_run=True,
            cutoff_iso="c",
            audit_rows=0,
            idempotency_rows=0,
            outbound_delivery_rows_matched=0,
            outbound_delivery_rows_deleted=0,
            rounds=0,
        )
    )

    async def fake_open_pool(_url: str) -> object:
        class P:
            def acquire(self) -> object:
                @asynccontextmanager
                async def _cm():
                    yield object()

                return _cm()

            async def close(self) -> None: ...

        return P()

    monkeypatch.setattr(scheduled_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(scheduled_mod, "load_retention_settings_from_env", lambda: loaded)
    monkeypatch.setattr(scheduled_mod, "_default_open_pool", fake_open_pool)
    monkeypatch.setattr(scheduled_mod, "run_slice1_retention_cleanup", cleanup)

    asyncio.run(scheduled_mod.run_slice1_retention_scheduled_from_env())

    cleanup.assert_awaited_once()
    (_conn,), kwargs = cleanup.call_args
    assert kwargs["settings"].dry_run is True

    out = capsys.readouterr().out
    assert "slice1_retention_scheduled_cleanup" in out
    assert "dry_run=True" in out


@pytest.mark.parametrize("raw", ("1", "true", "yes", " True "))
def test_scheduled_opt_in_respects_loaded_dry_run_false(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.setenv(ENV_SCHED, raw)

    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=_SYNTHETIC_DSN,
        app_env="development",
        debug_safe=False,
    )
    loaded = RetentionSettings(
        ttl_seconds=3600,
        batch_limit=100,
        dry_run=False,
        max_rounds=5,
    )
    cleanup = AsyncMock(
        return_value=RetentionCleanupResult(
            dry_run=False,
            cutoff_iso="c",
            audit_rows=0,
            idempotency_rows=0,
            outbound_delivery_rows_matched=0,
            outbound_delivery_rows_deleted=0,
            rounds=0,
        )
    )

    async def fake_open_pool(_url: str) -> object:
        class P:
            def acquire(self) -> object:
                @asynccontextmanager
                async def _cm():
                    yield object()

                return _cm()

            async def close(self) -> None: ...

        return P()

    monkeypatch.setattr(scheduled_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(scheduled_mod, "load_retention_settings_from_env", lambda: loaded)
    monkeypatch.setattr(scheduled_mod, "_default_open_pool", fake_open_pool)
    monkeypatch.setattr(scheduled_mod, "run_slice1_retention_cleanup", cleanup)

    asyncio.run(scheduled_mod.run_slice1_retention_scheduled_from_env())

    (_conn,), kwargs = cleanup.call_args
    assert kwargs["settings"].dry_run is False


def test_scheduled_opt_in_truthy_preserves_loaded_settings_pass_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.setenv(ENV_SCHED, "1")

    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=_SYNTHETIC_DSN,
        app_env="development",
        debug_safe=False,
    )
    loaded = RetentionSettings(
        ttl_seconds=7777,
        batch_limit=33,
        dry_run=False,
        max_rounds=9,
    )
    cleanup = AsyncMock(
        return_value=RetentionCleanupResult(
            dry_run=False,
            cutoff_iso="c",
            audit_rows=0,
            idempotency_rows=0,
            outbound_delivery_rows_matched=0,
            outbound_delivery_rows_deleted=0,
            rounds=0,
        )
    )

    async def fake_open_pool(_url: str) -> object:
        class P:
            def acquire(self) -> object:
                @asynccontextmanager
                async def _cm():
                    yield object()

                return _cm()

            async def close(self) -> None: ...

        return P()

    monkeypatch.setattr(scheduled_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(scheduled_mod, "load_retention_settings_from_env", lambda: loaded)
    monkeypatch.setattr(scheduled_mod, "_default_open_pool", fake_open_pool)
    monkeypatch.setattr(scheduled_mod, "run_slice1_retention_cleanup", cleanup)

    asyncio.run(scheduled_mod.run_slice1_retention_scheduled_from_env())

    cleanup.assert_awaited_once()
    (_conn,), kwargs = cleanup.call_args
    assert kwargs["settings"].dry_run is False
    assert kwargs["settings"].ttl_seconds == 7777
    assert kwargs["settings"].batch_limit == 33
    assert kwargs["settings"].max_rounds == 9


def test_scheduled_opt_in_with_loaded_dry_run_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.setenv(ENV_SCHED, "1")

    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=_SYNTHETIC_DSN,
        app_env="development",
        debug_safe=False,
    )
    loaded = RetentionSettings(
        ttl_seconds=3600,
        batch_limit=100,
        dry_run=True,
        max_rounds=5,
    )
    cleanup = AsyncMock(
        return_value=RetentionCleanupResult(
            dry_run=True,
            cutoff_iso="c",
            audit_rows=0,
            idempotency_rows=0,
            outbound_delivery_rows_matched=0,
            outbound_delivery_rows_deleted=0,
            rounds=0,
        )
    )

    async def fake_open_pool(_url: str) -> object:
        class P:
            def acquire(self) -> object:
                @asynccontextmanager
                async def _cm():
                    yield object()

                return _cm()

            async def close(self) -> None: ...

        return P()

    monkeypatch.setattr(scheduled_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(scheduled_mod, "load_retention_settings_from_env", lambda: loaded)
    monkeypatch.setattr(scheduled_mod, "_default_open_pool", fake_open_pool)
    monkeypatch.setattr(scheduled_mod, "run_slice1_retention_cleanup", cleanup)

    asyncio.run(scheduled_mod.run_slice1_retention_scheduled_from_env())

    (_conn,), kwargs = cleanup.call_args
    assert kwargs["settings"].dry_run is True


def test_scheduled_opt_in_cleanup_failure_closes_pool_and_sanitizes_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(ENV_SCHED, "1")

    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=_SYNTHETIC_DSN,
        app_env="development",
        debug_safe=False,
    )
    loaded = RetentionSettings(
        ttl_seconds=3600,
        batch_limit=10,
        dry_run=False,
        max_rounds=2,
    )

    class FakePool:
        def __init__(self) -> None:
            self.acquire_count = 0
            self.close_calls = 0
            self.closed = False

        def acquire(self) -> object:
            self.acquire_count += 1

            @asynccontextmanager
            async def _cm():
                yield object()

            return _cm()

        async def close(self) -> None:
            self.close_calls += 1
            self.closed = True

    pool = FakePool()

    async def fake_open_pool(_url: str) -> object:
        return pool

    cleanup = AsyncMock(side_effect=RuntimeError("cleanup failed"))

    monkeypatch.setattr(scheduled_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(scheduled_mod, "load_retention_settings_from_env", lambda: loaded)
    monkeypatch.setattr(scheduled_mod, "_default_open_pool", fake_open_pool)
    monkeypatch.setattr(scheduled_mod, "run_slice1_retention_cleanup", cleanup)

    with pytest.raises(RuntimeError, match="cleanup failed") as excinfo:
        asyncio.run(scheduled_mod.run_slice1_retention_scheduled_from_env())

    assert pool.acquire_count == 1
    assert pool.close_calls == 1
    assert pool.closed is True

    captured = capsys.readouterr()
    assert _classify_retention_boundary_exception_for_tests(excinfo.value) == "unexpected_error"
    assert_retention_failure_output_safe(
        excinfo.value,
        captured.out,
        captured.err,
        summary_stdout=captured.out,
        summary_stderr=captured.err,
        summary_markers=("slice1_retention_scheduled_cleanup",),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("database_url", (None, "", "   "))
async def test_missing_dsn_before_pool(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str | None,
) -> None:
    _valid_retention_env(monkeypatch)
    open_calls: list[object] = []

    async def no_pool(_u: str) -> object:  # pragma: no cover
        open_calls.append(True)
        return object()

    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=database_url,
        app_env="development",
        debug_safe=False,
    )
    monkeypatch.setattr(scheduled_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(scheduled_mod, "_default_open_pool", no_pool)
    with pytest.raises(ConfigurationError, match="DATABASE_URL") as exc_info:
        await scheduled_mod.run_slice1_retention_scheduled_from_env()
    assert _classify_retention_boundary_exception_for_tests(exc_info.value) == "config_error"
    assert not open_calls


@pytest.mark.asyncio
async def test_scheduled_dependency_failure_taxonomy_and_no_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _valid_retention_env(monkeypatch)
    monkeypatch.setenv(ENV_SCHED, "1")

    config = RuntimeConfig(
        bot_token="12345678901",
        database_url=_SYNTHETIC_DSN,
        app_env="development",
        debug_safe=False,
    )
    loaded = RetentionSettings(
        ttl_seconds=3600,
        batch_limit=100,
        dry_run=False,
        max_rounds=5,
    )

    async def fail_open_pool(_url: str) -> object:
        raise OSError("driver dependency unavailable")

    monkeypatch.setattr(scheduled_mod, "load_runtime_config", lambda: config)
    monkeypatch.setattr(scheduled_mod, "load_retention_settings_from_env", lambda: loaded)
    monkeypatch.setattr(scheduled_mod, "_default_open_pool", fail_open_pool)

    with pytest.raises(OSError) as exc_info:
        await scheduled_mod.run_slice1_retention_scheduled_from_env()

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
        summary_markers=("slice1_retention_scheduled_cleanup",),
    )


def test_main_delegates_to_asyncio_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def fake_run(awaitable: object) -> None:
        seen.append(awaitable)

    monkeypatch.setattr(scheduled_mod.asyncio, "run", fake_run)

    scheduled_mod.main()

    assert len(seen) == 1
    seen[0].close()
