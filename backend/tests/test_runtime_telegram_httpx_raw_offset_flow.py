"""E2E offset behavior for concrete httpx raw stack (client → bundle → app/process, MockTransport)."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

import app.runtime.telegram_httpx_raw_env as env_mod
from app.runtime.telegram_httpx_raw_app import Slice1HttpxRawRuntimeApp, build_slice1_httpx_raw_runtime_app
from app.runtime.telegram_httpx_raw_process import Slice1HttpxRawProcess, build_slice1_httpx_raw_process_from_env
from app.security.config import RuntimeConfig
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


def _json_body(request: httpx.Request) -> dict:
    if not request.content:
        return {}
    return json.loads(request.content.decode())


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


@pytest.mark.parametrize("use_process", [False, True])
def test_first_getupdates_json_omits_offset(use_process: bool) -> None:
    get_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            get_bodies.append(_json_body(request))
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            target = _build_target(ac, use_process=use_process)
            try:
                await target.poll_once(correlation_id=new_correlation_id())
            finally:
                await target.aclose()

    _run(main())
    assert len(get_bodies) == 1
    assert "offset" not in get_bodies[0]


@pytest.mark.parametrize("use_process", [False, True])
def test_second_getupdates_uses_max_update_id_plus_one(use_process: bool) -> None:
    u = _update(update_id=7, message=_base_message(text="/start"))
    get_bodies: list[dict] = []
    phase = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal phase
        if request.url.path.endswith("/getUpdates"):
            get_bodies.append(_json_body(request))
            phase_local = phase
            phase += 1
            if phase_local == 0:
                return httpx.Response(200, json={"ok": True, "result": [u]})
            return httpx.Response(200, json={"ok": True, "result": []})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            target = _build_target(ac, use_process=use_process)
            try:
                await target.poll_once(correlation_id=new_correlation_id())
                assert _offset(target) == 8
                await target.poll_once(correlation_id=new_correlation_id())
                assert _offset(target) == 8
            finally:
                await target.aclose()

    _run(main())
    assert len(get_bodies) == 2
    assert "offset" not in get_bodies[0]
    assert get_bodies[1].get("offset") == 8


@pytest.mark.parametrize("use_process", [False, True])
def test_no_valid_update_id_offset_not_advanced(use_process: bool) -> None:
    bad = {"update_id": "nope", "message": _base_message(text="/start")}
    get_bodies: list[dict] = []
    phase = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal phase
        if request.url.path.endswith("/getUpdates"):
            get_bodies.append(_json_body(request))
            phase_local = phase
            phase += 1
            if phase_local == 0:
                return httpx.Response(200, json={"ok": True, "result": [bad]})
            return httpx.Response(
                200,
                json={"ok": True, "result": [_update(update_id=9, message=_base_message(text="/start"))]},
            )
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            target = _build_target(ac, use_process=use_process)
            try:
                await target.poll_once(correlation_id=new_correlation_id())
                assert _offset(target) is None
                await target.poll_once(correlation_id=new_correlation_id())
                assert _offset(target) == 10
            finally:
                await target.aclose()

    _run(main())
    assert len(get_bodies) == 2
    assert "offset" not in get_bodies[0]
    assert "offset" not in get_bodies[1]


@pytest.mark.parametrize("use_process", [False, True])
def test_getupdates_http_error_does_not_change_offset(use_process: bool) -> None:
    u = _update(update_id=5, message=_base_message(text="/start"))
    phase = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal phase
        if request.url.path.endswith("/getUpdates"):
            phase += 1
            if phase == 1:
                return httpx.Response(200, json={"ok": True, "result": [u]})
            return httpx.Response(500, json={"ok": False})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            target = _build_target(ac, use_process=use_process)
            try:
                r1 = await target.poll_once(correlation_id=new_correlation_id())
                assert r1.fetch_failure_count == 0
                assert _offset(target) == 6
                r2 = await target.poll_once(correlation_id=new_correlation_id())
                assert r2.fetch_failure_count == 1
                assert _offset(target) == 6
            finally:
                await target.aclose()

    _run(main())
