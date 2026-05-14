"""E2E sendMessage failure on concrete httpx live stack (app + process, MockTransport)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

import app.runtime.telegram_httpx_live_env as env_mod
from app.runtime.telegram_httpx_live_app import build_slice1_httpx_live_runtime_app
from app.runtime.telegram_httpx_live_process import (
    Slice1HttpxLiveProcess,
    build_slice1_httpx_live_process_from_env,
)
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


def _start_update(*, update_id: int = 11, user_id: int = 42) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": user_id, "is_bot": False, "first_name": "U"},
            "chat": {"id": user_id, "type": "private"},
            "text": "/start",
        },
    }


def _send_fail_response(mode: str) -> httpx.Response:
    if mode == "http_error":
        return httpx.Response(500, json={"ok": False, "description": "internal"})
    if mode == "ok_false":
        return httpx.Response(200, json={"ok": False, "description": "bad request"})
    raise ValueError(mode)


def _assert_send_failure_summary(s) -> None:
    assert s.send_failure_count == 1
    assert s.fetch_failure_count == 0
    assert s.processing_failure_count == 0
    assert s.send_count == 0
    assert s.received_count == 1


@pytest.mark.parametrize("send_fail_mode", ["http_error", "ok_false"])
def test_live_app_run_iterations_start_send_fails_counters_offset_audit(send_fail_mode: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [_start_update()]})
        if request.url.path.endswith("/sendMessage"):
            return _send_fail_response(send_fail_mode)
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app("t", client=ac)
            try:
                s = await app.run_iterations(1, correlation_id=new_correlation_id())
                _assert_send_failure_summary(s)
                assert app.bundle.bundle.runtime.current_offset == 12
                assert len(await app.bundle.bundle.composition.audit.recorded_events()) == 1
            finally:
                await app.aclose()

    _run(main())


@pytest.mark.parametrize("send_fail_mode", ["http_error", "ok_false"])
def test_live_process_run_until_stopped_start_send_fails_counters_offset_audit(
    send_fail_mode: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [_start_update()]})
        if request.url.path.endswith("/sendMessage"):
            return _send_fail_response(send_fail_mode)
        return httpx.Response(404)

    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                proc: Slice1HttpxLiveProcess = build_slice1_httpx_live_process_from_env(client=ac)
            try:
                s = await proc.run_until_stopped(
                    correlation_id=new_correlation_id(),
                    max_iterations=1,
                )
                _assert_send_failure_summary(s)
                assert proc.app.bundle.bundle.runtime.current_offset == 12
                assert len(await proc.app.bundle.bundle.composition.audit.recorded_events()) == 1
            finally:
                await proc.aclose()

    _run(main())


@pytest.mark.parametrize("use_app", [True, False])
def test_live_send_failure_is_not_fetch_failure(use_app: bool) -> None:
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
            if use_app:
                target = build_slice1_httpx_live_runtime_app("t", client=ac)
            else:
                with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                    target = build_slice1_httpx_live_process_from_env(client=ac)
            try:
                if use_app:
                    s = await target.run_iterations(1, correlation_id=new_correlation_id())
                else:
                    s = await target.run_until_stopped(
                        correlation_id=new_correlation_id(),
                        max_iterations=1,
                    )
                assert s.fetch_failure_count == 0 and s.send_failure_count == 1
            finally:
                await target.aclose()

    _run(main())
