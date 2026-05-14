"""E2E sendMessage failure on concrete httpx raw stack (app + process, MockTransport)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

import app.runtime.telegram_httpx_raw_env as env_mod
from app.runtime.telegram_httpx_raw_app import Slice1HttpxRawRuntimeApp, build_slice1_httpx_raw_runtime_app
from app.runtime.telegram_httpx_raw_process import Slice1HttpxRawProcess, build_slice1_httpx_raw_process_from_env
from app.security.config import RuntimeConfig
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


def _base_message(*, text: str, user_id: int = 42, chat_type: str = "private") -> dict[str, object]:
    return {
        "message_id": 1,
        "from": {"id": user_id, "is_bot": False, "first_name": "U"},
        "chat": {"id": user_id, "type": chat_type},
        "text": text,
    }


def _update(
    *,
    update_id: int = 1,
    message: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    u: dict[str, object] = {"update_id": update_id, "message": message}
    u.update(extra)
    return u


def _minimal_runtime_config(*, bot_token: str = "1234567890tok") -> RuntimeConfig:
    return RuntimeConfig(
        bot_token=bot_token,
        database_url="postgresql://localhost/db",
        app_env="development",
        debug_safe=False,
    )


def _offset(target: Slice1HttpxRawRuntimeApp | Slice1HttpxRawProcess) -> int | None:
    if isinstance(target, Slice1HttpxRawProcess):
        return target.app.bundle.bundle.runtime.current_offset
    return target.bundle.bundle.runtime.current_offset


def _audit(target: Slice1HttpxRawRuntimeApp | Slice1HttpxRawProcess):
    if isinstance(target, Slice1HttpxRawProcess):
        return target.app.bundle.bundle.composition.audit
    return target.bundle.bundle.composition.audit


def _build_target(
    ac: httpx.AsyncClient,
    *,
    use_process: bool,
    cfg: RuntimeConfig | None = None,
) -> Slice1HttpxRawRuntimeApp | Slice1HttpxRawProcess:
    base = "https://ex.invalid/bot/"
    if use_process:
        resolved = cfg or _minimal_runtime_config()
        with patch.object(env_mod, "load_runtime_config", return_value=resolved):
            return build_slice1_httpx_raw_process_from_env(client=ac)
    return build_slice1_httpx_raw_runtime_app("tok", base_url=base, client=ac)


def _send_fail_response(mode: str) -> httpx.Response:
    if mode == "http_error":
        return httpx.Response(500, json={"ok": False, "description": "internal"})
    if mode == "ok_false":
        return httpx.Response(200, json={"ok": False, "description": "bad request"})
    raise ValueError(mode)


@pytest.mark.parametrize("use_process", [False, True])
@pytest.mark.parametrize("send_fail_mode", ["http_error", "ok_false"])
def test_poll_once_start_send_fails_counters_and_offset(use_process: bool, send_fail_mode: str) -> None:
    raw = _update(update_id=11, message=_base_message(text="/start"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [raw]})
        if request.url.path.endswith("/sendMessage"):
            return _send_fail_response(send_fail_mode)
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            target = _build_target(ac, use_process=use_process)
            try:
                r = await target.poll_once(correlation_id=new_correlation_id())
                assert r.fetch_failure_count == 0
                assert r.processing_failure_count == 0
                assert r.send_failure_count == 1
                assert r.send_count == 0
                assert r.raw_received_count == 1
                assert _offset(target) == 12
                assert len(await _audit(target).recorded_events()) == 1
            finally:
                await target.aclose()

    _run(main())


@pytest.mark.parametrize("use_process", [False, True])
def test_send_failure_is_not_fetch_failure(use_process: bool) -> None:
    """Regression: outbound failure must not increment fetch_failure_count."""
    raw = _update(update_id=3, message=_base_message(text="/start"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [raw]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(503, json={"ok": False})
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            target = _build_target(ac, use_process=use_process)
            try:
                r = await target.poll_once(correlation_id=new_correlation_id())
                assert r.fetch_failure_count == 0 and r.send_failure_count == 1
            finally:
                await target.aclose()

    _run(main())
