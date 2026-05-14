"""E2E send/fetch failure via :func:`run_slice1_httpx_raw_iterations` (non-env, MockTransport)."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

import app.runtime.telegram_httpx_raw_runner as httpx_raw_runner_mod
from app.runtime.runner import PollingRunSummary
from app.runtime.telegram_httpx_raw_runner import run_slice1_httpx_raw_iterations
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


def _json_body(request: httpx.Request) -> dict:
    if not request.content:
        return {}
    return json.loads(request.content.decode())


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


def _patch_capture_bundle(captured: list):
    real_build = httpx_raw_runner_mod.build_slice1_httpx_raw_runtime_bundle

    def _wrap(*args, **kwargs):
        b = real_build(*args, **kwargs)
        captured.append(b)
        return b

    return patch.object(
        httpx_raw_runner_mod,
        "build_slice1_httpx_raw_runtime_bundle",
        side_effect=_wrap,
    )


@pytest.mark.parametrize("send_fail_mode", ["http_error", "ok_false"])
def test_httpx_raw_iterations_sendmessage_fails_summary_offset_audit(send_fail_mode: str) -> None:
    uid = 11
    raw = _update(update_id=uid, message=_base_message(text="/start"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [raw]})
        if request.url.path.endswith("/sendMessage"):
            return _send_fail_response(send_fail_mode)
        return httpx.Response(404)

    captured: list = []

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with _patch_capture_bundle(captured):
                s = await run_slice1_httpx_raw_iterations(
                    "tok",
                    1,
                    base_url="https://ex.invalid/bot/",
                    client=ac,
                    correlation_id=new_correlation_id(),
                )
        _assert_single_iteration_send_failure_summary(s)
        assert len(captured) == 1
        assert captured[0].bundle.runtime.current_offset == uid + 1
        assert len(await captured[0].bundle.composition.audit.recorded_events()) == 1

    _run(main())


def test_httpx_raw_iterations_send_failure_not_fetch_failure() -> None:
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
            s = await run_slice1_httpx_raw_iterations(
                "tok",
                1,
                base_url="https://ex.invalid/bot/",
                client=ac,
            )
        assert s.send_failure_count == 1 and s.fetch_failure_count == 0

    _run(main())


@pytest.mark.parametrize("fetch_fail_mode", ["http_error", "ok_false"])
def test_httpx_raw_iterations_getupdates_fails_no_send_empty_audit(fetch_fail_mode: str) -> None:
    send_hits: list[None] = []
    get_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            try:
                get_bodies.append(_json_body(request))
            except (json.JSONDecodeError, UnicodeDecodeError):
                get_bodies.append({})
            return _fetch_fail_response(fetch_fail_mode)
        if request.url.path.endswith("/sendMessage"):
            send_hits.append(None)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    captured: list = []

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with _patch_capture_bundle(captured):
                s = await run_slice1_httpx_raw_iterations(
                    "tok",
                    1,
                    base_url="https://ex.invalid/bot/",
                    client=ac,
                    correlation_id=new_correlation_id(),
                )
        _assert_single_iteration_fetch_failure_summary(s)
        assert not send_hits
        assert len(get_bodies) == 1 and "offset" not in get_bodies[0]
        assert len(captured) == 1
        assert len(await captured[0].bundle.composition.audit.recorded_events()) == 0

    _run(main())


def test_httpx_raw_iterations_fetch_failure_not_send_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(503, json={"ok": False})
        if request.url.path.endswith("/sendMessage"):
            raise AssertionError("sendMessage must not be called on fetch failure")
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            s = await run_slice1_httpx_raw_iterations(
                "tok",
                1,
                base_url="https://ex.invalid/bot/",
                client=ac,
            )
        assert s.fetch_failure_count == 1 and s.send_failure_count == 0

    _run(main())


@pytest.mark.parametrize("fetch_fail_mode", ["http_error", "ok_false"])
def test_httpx_raw_iterations_success_then_fetch_fail_second_getupdates_uses_offset_8(
    fetch_fail_mode: str,
) -> None:
    u = _update(update_id=7, message=_base_message(text="/start"))
    phase = 0
    get_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal phase
        if request.url.path.endswith("/getUpdates"):
            try:
                get_bodies.append(_json_body(request))
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

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            s = await run_slice1_httpx_raw_iterations(
                "tok",
                2,
                base_url="https://ex.invalid/bot/",
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
