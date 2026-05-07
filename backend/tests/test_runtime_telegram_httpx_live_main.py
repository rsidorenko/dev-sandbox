"""Tests for :mod:`app.runtime.telegram_httpx_live_main`."""

from __future__ import annotations

import asyncio
import logging
import signal
from unittest.mock import AsyncMock, Mock

import pytest

import app.runtime as rt
import app.runtime.telegram_httpx_live_main as main_mod
from app.runtime.runner import PollingRunSummary
from app.runtime.telegram_httpx_live_main import run_slice1_httpx_live_from_env


def _summary() -> PollingRunSummary:
    return PollingRunSummary(
        iterations_requested=0,
        iterations_completed=0,
        received_count=0,
        send_count=0,
        noop_count=0,
        send_failure_count=0,
        processing_failure_count=0,
        fetch_failure_count=0,
        poll_once_exception_count=0,
    )


def test_run_from_env_delegates_to_builder_and_process_calls_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = AsyncMock()
    expected = _summary()
    fake_process.run_until_stopped.return_value = expected

    build = AsyncMock(return_value=fake_process)
    monkeypatch.setattr(main_mod, "build_slice1_httpx_live_process_from_env_async", build)

    result = asyncio.run(run_slice1_httpx_live_from_env())

    assert result is expected
    build.assert_awaited_once_with()
    fake_process.run_until_stopped.assert_awaited_once_with()
    fake_process.aclose.assert_awaited_once_with()


def test_run_from_env_always_closes_on_run_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_process = AsyncMock()
    fake_process.run_until_stopped.side_effect = RuntimeError(
        "boom token=123:ABC database=postgres://user:pass@host/db"
    )

    build = AsyncMock(return_value=fake_process)
    monkeypatch.setattr(main_mod, "build_slice1_httpx_live_process_from_env_async", build)

    caplog.set_level(logging.INFO, logger=main_mod.__name__)
    with pytest.raises(RuntimeError, match="boom token=123:ABC"):
        asyncio.run(run_slice1_httpx_live_from_env())

    build.assert_awaited_once_with()
    fake_process.run_until_stopped.assert_awaited_once_with()
    fake_process.aclose.assert_awaited_once_with()
    error_records = [
        record
        for record in caplog.records
        if record.getMessage() == "runtime.live.entrypoint.failed"
    ]
    assert len(error_records) == 1
    assert error_records[0].structured_fields == {
        "intent": "runtime_loop",
        "outcome": "error",
        "operation": "run_until_stopped",
        "internal_category": "runtime_exception",
    }
    assert "123:ABC" not in caplog.text
    assert "postgres://user:pass@host/db" not in caplog.text


def test_run_from_env_logs_startup_failure_and_reraises_without_close(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    startup_error = RuntimeError(
        "startup failed token=999:SECRET database=postgres://user:pass@host/db"
    )
    build = AsyncMock(side_effect=startup_error)
    register_handlers = Mock()
    monkeypatch.setattr(main_mod, "build_slice1_httpx_live_process_from_env_async", build)
    monkeypatch.setattr(main_mod, "_register_signal_stop_handlers", register_handlers)

    caplog.set_level(logging.INFO, logger=main_mod.__name__)
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(run_slice1_httpx_live_from_env())

    assert exc_info.value is startup_error
    build.assert_awaited_once_with()
    register_handlers.assert_not_called()
    error_records = [
        record
        for record in caplog.records
        if record.getMessage() == "runtime.live.entrypoint.failed"
    ]
    assert len(error_records) == 1
    assert error_records[0].structured_fields == {
        "intent": "startup",
        "outcome": "error",
        "operation": "build_process",
        "internal_category": "startup_exception",
    }
    lifecycle_records = [
        record
        for record in caplog.records
        if record.getMessage() == "runtime.live.entrypoint.lifecycle"
    ]
    assert len(lifecycle_records) == 1
    assert lifecycle_records[0].structured_fields == {
        "intent": "startup",
        "outcome": "begin",
        "operation": "run_until_stopped",
    }
    assert "999:SECRET" not in caplog.text
    assert "postgres://user:pass@host/db" not in caplog.text


def test_register_signal_stop_handlers_uses_loop_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = Mock()
    captured: list[tuple[signal.Signals, object]] = []

    class FakeLoop:
        def add_signal_handler(self, signum: signal.Signals, callback: object) -> None:
            captured.append((signum, callback))

    monkeypatch.setattr(main_mod.asyncio, "get_running_loop", lambda: FakeLoop())

    main_mod._register_signal_stop_handlers(fake_process)

    registered = {signum for signum, _ in captured}
    assert signal.SIGINT in registered
    assert signal.SIGTERM in registered


def test_register_signal_stop_handlers_callback_requests_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = Mock()
    callbacks: dict[signal.Signals, object] = {}

    class FakeLoop:
        def add_signal_handler(self, signum: signal.Signals, callback: object) -> None:
            callbacks[signum] = callback

    monkeypatch.setattr(main_mod.asyncio, "get_running_loop", lambda: FakeLoop())

    main_mod._register_signal_stop_handlers(fake_process)

    callback = callbacks[signal.SIGINT]
    assert callable(callback)
    callback()
    fake_process.request_stop.assert_called_once_with()


def test_register_signal_stop_handlers_skips_platform_limitations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = Mock()

    class FakeLoop:
        def add_signal_handler(self, signum: signal.Signals, callback: object) -> None:
            raise NotImplementedError("signals unsupported")

    monkeypatch.setattr(main_mod.asyncio, "get_running_loop", lambda: FakeLoop())

    main_mod._register_signal_stop_handlers(fake_process)

    fake_process.request_stop.assert_not_called()


def test_run_from_env_ignores_signal_registration_limitations_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = AsyncMock()
    expected = _summary()
    fake_process.run_until_stopped.return_value = expected

    build = AsyncMock(return_value=fake_process)
    monkeypatch.setattr(main_mod, "build_slice1_httpx_live_process_from_env_async", build)
    monkeypatch.setattr(
        main_mod,
        "_register_signal_stop_handlers",
        lambda process: (_ for _ in ()).throw(NotImplementedError("unsupported")),
    )

    result = asyncio.run(run_slice1_httpx_live_from_env())

    assert result is expected
    fake_process.run_until_stopped.assert_awaited_once_with()
    fake_process.aclose.assert_awaited_once_with()


def test_run_from_env_logs_start_and_normal_completion(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_process = AsyncMock()
    expected = _summary()
    fake_process.run_until_stopped.return_value = expected

    build = AsyncMock(return_value=fake_process)
    monkeypatch.setattr(main_mod, "build_slice1_httpx_live_process_from_env_async", build)

    caplog.set_level(logging.INFO, logger=main_mod.__name__)
    result = asyncio.run(run_slice1_httpx_live_from_env())

    assert result is expected
    lifecycle_records = [
        record
        for record in caplog.records
        if record.getMessage() == "runtime.live.entrypoint.lifecycle"
    ]
    assert len(lifecycle_records) == 3
    assert lifecycle_records[0].structured_fields == {
        "intent": "startup",
        "outcome": "begin",
        "operation": "run_until_stopped",
    }
    assert lifecycle_records[1].structured_fields == {
        "intent": "startup",
        "outcome": "completed",
        "operation": "set_my_commands",
    }
    assert lifecycle_records[2].structured_fields == {
        "intent": "shutdown",
        "outcome": "completed",
        "operation": "run_until_stopped",
    }


def test_signal_callback_logs_shutdown_request(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_process = Mock()
    callbacks: dict[signal.Signals, object] = {}

    class FakeLoop:
        def add_signal_handler(self, signum: signal.Signals, callback: object) -> None:
            callbacks[signum] = callback

    monkeypatch.setattr(main_mod.asyncio, "get_running_loop", lambda: FakeLoop())
    caplog.set_level(logging.INFO, logger=main_mod.__name__)

    main_mod._register_signal_stop_handlers(fake_process)

    callback = callbacks[signal.SIGTERM]
    assert callable(callback)
    callback()

    fake_process.request_stop.assert_called_once_with()
    lifecycle_records = [
        record
        for record in caplog.records
        if record.getMessage() == "runtime.live.entrypoint.lifecycle"
    ]
    assert len(lifecycle_records) == 1
    assert lifecycle_records[0].structured_fields == {
        "intent": "shutdown_request",
        "outcome": "signal_received",
        "operation": "signal:SIGTERM",
    }


def test_main_uses_asyncio_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_asyncio_run(coro: object) -> None:
        captured["is_coroutine"] = asyncio.iscoroutine(coro)
        if asyncio.iscoroutine(coro):
            coro.close()

    monkeypatch.setattr(main_mod.asyncio, "run", fake_asyncio_run)

    main_mod.main()

    assert captured["is_coroutine"] is True


def test_app_runtime_export() -> None:
    from app.runtime import run_slice1_httpx_live_from_env as rt_run

    assert rt.run_slice1_httpx_live_from_env is run_slice1_httpx_live_from_env
    assert rt_run is run_slice1_httpx_live_from_env
    assert "run_slice1_httpx_live_from_env" in rt.__all__
