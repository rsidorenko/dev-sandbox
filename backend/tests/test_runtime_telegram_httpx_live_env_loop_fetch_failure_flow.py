"""E2E fetch/send failure through :func:`run_slice1_httpx_live_until_stopped_from_env` (MockTransport, patched config)."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

import app.runtime.telegram_httpx_live_env as env_mod
import app.runtime.telegram_httpx_live_env_loop as env_loop_mod
from app.runtime.live_loop import LoopControl, Slice1LiveRawPollingLoop
from app.runtime.telegram_httpx_live_app import Slice1HttpxLiveRuntimeApp
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


def _assert_first_tick_fetch_failure_summary(s) -> None:
    assert s.fetch_failure_count == 1
    assert s.send_failure_count == 0
    assert s.processing_failure_count == 0
    assert s.send_count == 0
    assert s.received_count == 0
    assert s.noop_count == 0


def _assert_env_loop_send_failure_first_tick_summary(s) -> None:
    assert s.iterations_requested == 1
    assert s.iterations_completed == 1
    assert s.poll_once_exception_count == 0
    assert s.send_failure_count == 1
    assert s.fetch_failure_count == 0
    assert s.processing_failure_count == 0
    assert s.send_count == 0
    assert s.received_count == 1
    assert s.noop_count == 0


def _patch_capture_app(captured: list[Slice1HttpxLiveRuntimeApp]):
    real_build = env_loop_mod.build_slice1_httpx_live_runtime_app_from_env_async

    async def _wrap(**kwargs):
        app = await real_build(**kwargs)
        captured.append(app)
        return app

    return patch.object(
        env_loop_mod,
        "build_slice1_httpx_live_runtime_app_from_env_async",
        side_effect=_wrap,
    )


@pytest.mark.parametrize("send_fail_mode", ["http_error", "ok_false"])
def test_env_loop_send_fails_counters_offset_audit(send_fail_mode: str) -> None:
    uid = 17
    u = _start_update(update_id=uid)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [u]})
        if request.url.path.endswith("/sendMessage"):
            return _send_fail_response(send_fail_mode)
        return httpx.Response(404)

    cfg = _minimal_runtime_config()
    captured: list[Slice1HttpxLiveRuntimeApp] = []

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with _patch_capture_app(captured):
                    s = await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                        LoopControl(),
                        client=ac,
                        correlation_id=new_correlation_id(),
                        max_iterations=1,
                    )
            _assert_env_loop_send_failure_first_tick_summary(s)
            assert len(captured) == 1
            assert captured[0].bundle.bundle.runtime.current_offset == uid + 1
            assert len(await captured[0].bundle.bundle.composition.audit.recorded_events()) == 1

    _run(main())


def test_env_loop_send_failure_is_not_fetch_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [_start_update(update_id=3)]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(503, json={"ok": False})
        return httpx.Response(404)

    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                s = await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                    LoopControl(),
                    client=ac,
                    correlation_id=new_correlation_id(),
                    max_iterations=1,
                )
            assert s.fetch_failure_count == 0 and s.send_failure_count == 1

    _run(main())


@pytest.mark.parametrize("fetch_fail_mode", ["http_error", "ok_false"])
def test_env_loop_getupdates_fails_counters_no_send_no_audit(fetch_fail_mode: str) -> None:
    send_hits: list[None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return _fetch_fail_response(fetch_fail_mode)
        if request.url.path.endswith("/sendMessage"):
            send_hits.append(None)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    cfg = _minimal_runtime_config()
    captured: list[Slice1HttpxLiveRuntimeApp] = []

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with _patch_capture_app(captured):
                    s = await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                        LoopControl(),
                        client=ac,
                        correlation_id=new_correlation_id(),
                        max_iterations=1,
                    )
            _assert_first_tick_fetch_failure_summary(s)
            assert not send_hits
            assert len(captured) == 1
            assert len(await captured[0].bundle.bundle.composition.audit.recorded_events()) == 0

    _run(main())


@pytest.mark.parametrize("fetch_fail_mode", ["http_error", "ok_false"])
def test_env_loop_offset_preserved_after_success_then_fetch_failure(fetch_fail_mode: str) -> None:
    u = _start_update(update_id=7)
    phase = 0
    get_updates_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal phase
        if request.url.path.endswith("/getUpdates"):
            try:
                get_updates_bodies.append(json.loads(request.content.decode()))
            except (json.JSONDecodeError, UnicodeDecodeError):
                get_updates_bodies.append({})
            phase_local = phase
            phase += 1
            if phase_local == 0:
                return httpx.Response(200, json={"ok": True, "result": [u]})
            return _fetch_fail_response(fetch_fail_mode)
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    cfg = _minimal_runtime_config()
    captured: list[Slice1HttpxLiveRuntimeApp] = []

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with _patch_capture_app(captured):
                    s = await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                        LoopControl(),
                        client=ac,
                        correlation_id=new_correlation_id(),
                        max_iterations=2,
                    )
            assert s.fetch_failure_count == 1
            assert s.send_failure_count == 0
            assert s.received_count == 1
            assert len(get_updates_bodies) == 2
            assert get_updates_bodies[1].get("offset") == 8
            assert len(captured) == 1
            assert captured[0].bundle.bundle.runtime.current_offset == 8
            assert len(await captured[0].bundle.bundle.composition.audit.recorded_events()) == 1

    _run(main())


def test_env_loop_fetch_failure_does_not_invoke_send_path() -> None:
    send_hits: list[None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(503, json={"ok": False})
        if request.url.path.endswith("/sendMessage"):
            send_hits.append(None)
            return httpx.Response(500, json={"ok": False})
        return httpx.Response(404)

    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                s = await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                    LoopControl(),
                    client=ac,
                    correlation_id=new_correlation_id(),
                    max_iterations=1,
                )
            assert s.fetch_failure_count == 1 and s.send_failure_count == 0
            assert not send_hits

    _run(main())


def test_env_loop_aclose_when_run_until_stopped_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _minimal_runtime_config()
    aclose_calls = 0
    real_aclose = Slice1HttpxLiveRuntimeApp.aclose

    async def counting_aclose(self: Slice1HttpxLiveRuntimeApp) -> None:
        nonlocal aclose_calls
        aclose_calls += 1
        await real_aclose(self)

    async def boom(
        self: Slice1LiveRawPollingLoop,
        control: LoopControl,
        *,
        correlation_id: str | None = None,
        max_iterations: int | None = None,
    ):
        raise RuntimeError("forced loop failure")

    monkeypatch.setattr(Slice1HttpxLiveRuntimeApp, "aclose", counting_aclose)
    monkeypatch.setattr(Slice1LiveRawPollingLoop, "run_until_stopped", boom)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with pytest.raises(RuntimeError, match="forced loop failure"):
                    await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                        LoopControl(),
                        client=ac,
                    )
        assert aclose_calls == 1

    _run(main())
