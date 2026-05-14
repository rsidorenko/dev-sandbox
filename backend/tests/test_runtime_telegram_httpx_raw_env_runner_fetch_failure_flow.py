"""E2E fetch/send failure through :func:`run_slice1_httpx_raw_iterations_from_env` (MockTransport, patched config)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import app.runtime.telegram_httpx_raw_env as env_mod
import app.runtime.telegram_httpx_raw_env_runner as env_runner_mod
from app.runtime.runner import PollingRunSummary
from app.runtime.telegram_httpx_raw_app import Slice1HttpxRawRuntimeApp
from app.runtime.telegram_httpx_raw_env_runner import run_slice1_httpx_raw_iterations_from_env
from app.security.config import RuntimeConfig
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


def _minimal_runtime_config(*, bot_token: str = "1234567890tok") -> RuntimeConfig:
    return RuntimeConfig(
        bot_token=bot_token,
        database_url="postgresql://localhost/db",
        app_env="development",
        debug_safe=False,
    )


def _start_update(*, update_id: int = 7, user_id: int = 42) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": user_id, "is_bot": False, "first_name": "U"},
            "chat": {"id": user_id, "type": "private"},
            "text": "/start",
        },
    }


def _fetch_fail_response(mode: str) -> httpx.Response:
    if mode == "http_error":
        return httpx.Response(500, json={"ok": False, "description": "internal"})
    if mode == "ok_false":
        return httpx.Response(200, json={"ok": False, "description": "bad request"})
    raise ValueError(mode)


def _send_fail_response(mode: str) -> httpx.Response:
    if mode == "http_error":
        return httpx.Response(500, json={"ok": False, "description": "internal"})
    if mode == "ok_false":
        return httpx.Response(200, json={"ok": False, "description": "bad request"})
    raise ValueError(mode)


def _assert_single_iteration_fetch_failure_summary(s: PollingRunSummary) -> None:
    assert s.iterations_requested == 1
    assert s.iterations_completed == 1
    assert s.poll_once_exception_count == 0
    assert s.fetch_failure_count == 1
    assert s.send_failure_count == 0
    assert s.processing_failure_count == 0
    assert s.send_count == 0
    assert s.received_count == 0
    assert s.noop_count == 0


def _assert_single_iteration_send_failure_summary(s: PollingRunSummary) -> None:
    assert s.iterations_requested == 1
    assert s.iterations_completed == 1
    assert s.poll_once_exception_count == 0
    assert s.send_failure_count == 1
    assert s.fetch_failure_count == 0
    assert s.processing_failure_count == 0
    assert s.send_count == 0
    assert s.received_count == 1
    assert s.noop_count == 0


def _patch_capture_app(captured: list[Slice1HttpxRawRuntimeApp]):
    real_build = env_runner_mod.build_slice1_httpx_raw_runtime_app_from_env

    def _wrap(**kwargs):
        app = real_build(**kwargs)
        captured.append(app)
        return app

    return patch.object(
        env_runner_mod,
        "build_slice1_httpx_raw_runtime_app_from_env",
        side_effect=_wrap,
    )


@pytest.mark.parametrize("fetch_fail_mode", ["http_error", "ok_false"])
def test_from_env_getupdates_fails_counters_no_send_no_audit(fetch_fail_mode: str) -> None:
    send_hits: list[None] = []
    get_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            try:
                get_bodies.append(json.loads(request.content.decode()))
            except (json.JSONDecodeError, UnicodeDecodeError):
                get_bodies.append({})
            return _fetch_fail_response(fetch_fail_mode)
        if request.url.path.endswith("/sendMessage"):
            send_hits.append(None)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    cfg = _minimal_runtime_config()
    captured: list[Slice1HttpxRawRuntimeApp] = []

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with _patch_capture_app(captured):
                    s = await run_slice1_httpx_raw_iterations_from_env(
                        1,
                        client=ac,
                        correlation_id=new_correlation_id(),
                    )
            _assert_single_iteration_fetch_failure_summary(s)
            assert not send_hits
            assert len(get_bodies) == 1 and "offset" not in get_bodies[0]
            assert len(captured) == 1
            assert len(await captured[0].bundle.bundle.composition.audit.recorded_events()) == 0

    _run(main())


@pytest.mark.parametrize("fetch_fail_mode", ["http_error", "ok_false"])
def test_from_env_first_success_advances_offset_second_fetch_fails_offset_preserved(fetch_fail_mode: str) -> None:
    u = _start_update(update_id=7)
    phase = 0
    get_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal phase
        if request.url.path.endswith("/getUpdates"):
            try:
                get_bodies.append(json.loads(request.content.decode()))
            except (json.JSONDecodeError, UnicodeDecodeError):
                get_bodies.append({})
            phase_local = phase
            phase += 1
            if phase_local == 0:
                return httpx.Response(200, json={"ok": True, "result": [u]})
            return _fetch_fail_response(fetch_fail_mode)
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                s = await run_slice1_httpx_raw_iterations_from_env(
                    2,
                    client=ac,
                    correlation_id=new_correlation_id(),
                )
        assert s.iterations_completed == 2
        assert s.received_count == 1
        assert s.send_count == 1
        assert s.fetch_failure_count == 1
        assert s.send_failure_count == 0
        assert s.processing_failure_count == 0
        assert len(get_bodies) == 2
        assert "offset" not in get_bodies[0]
        assert get_bodies[1].get("offset") == 8

    _run(main())


def test_from_env_fetch_failure_does_not_increment_send_failure() -> None:
    """Regression: inbound fetch failure must not be counted as send_failure_count."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(503, json={"ok": False})
        if request.url.path.endswith("/sendMessage"):
            raise AssertionError("sendMessage must not be called on fetch failure")
        return httpx.Response(404)

    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                s = await run_slice1_httpx_raw_iterations_from_env(1, client=ac)
        assert s.fetch_failure_count == 1 and s.send_failure_count == 0

    _run(main())


@pytest.mark.parametrize("send_fail_mode", ["http_error", "ok_false"])
def test_from_env_sendmessage_fails_counters_offset_audit(send_fail_mode: str) -> None:
    uid = 13
    u = _start_update(update_id=uid)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [u]})
        if request.url.path.endswith("/sendMessage"):
            return _send_fail_response(send_fail_mode)
        return httpx.Response(404)

    cfg = _minimal_runtime_config()
    captured: list[Slice1HttpxRawRuntimeApp] = []

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with _patch_capture_app(captured):
                    s = await run_slice1_httpx_raw_iterations_from_env(
                        1,
                        client=ac,
                        correlation_id=new_correlation_id(),
                    )
            _assert_single_iteration_send_failure_summary(s)
            assert len(captured) == 1
            assert captured[0].bundle.bundle.runtime.current_offset == uid + 1
            assert len(await captured[0].bundle.bundle.composition.audit.recorded_events()) == 1

    _run(main())


def test_from_env_send_failure_does_not_increment_fetch_failure() -> None:
    """Regression: outbound send failure must not be counted as fetch_failure_count."""

    u = _start_update(update_id=3)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [u]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(503, json={"ok": False})
        return httpx.Response(404)

    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                s = await run_slice1_httpx_raw_iterations_from_env(1, client=ac)
        assert s.send_failure_count == 1 and s.fetch_failure_count == 0

    _run(main())


def test_from_env_aclose_when_run_iterations_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _minimal_runtime_config()
    aclose_calls = 0
    orig_aclose = Slice1HttpxRawRuntimeApp.aclose

    async def counting_aclose(self: Slice1HttpxRawRuntimeApp) -> None:
        nonlocal aclose_calls
        aclose_calls += 1
        await orig_aclose(self)

    monkeypatch.setattr(Slice1HttpxRawRuntimeApp, "aclose", counting_aclose)
    monkeypatch.setattr(
        Slice1HttpxRawRuntimeApp,
        "run_iterations",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                try:
                    await run_slice1_httpx_raw_iterations_from_env(1, client=ac)
                except RuntimeError as exc:
                    assert str(exc) == "boom"
                else:
                    raise AssertionError("expected RuntimeError")
        assert aclose_calls == 1

    _run(main())
