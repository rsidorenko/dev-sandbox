"""E2E send/fetch failure through :func:`run_slice1_httpx_live_iterations` (MockTransport, no env)."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

import app.runtime.telegram_httpx_live_runner as runner_mod
from app.runtime.telegram_httpx_live_runner import run_slice1_httpx_live_iterations
from app.runtime.telegram_httpx_live_startup import (
    Slice1HttpxLiveRuntimeBundle,
)
from app.runtime.telegram_httpx_live_startup import (
    build_slice1_httpx_live_runtime_bundle as _orig_build,
)
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


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


def _fetch_fail_response(mode: str) -> httpx.Response:
    if mode == "http_error":
        return httpx.Response(500, json={"ok": False, "description": "internal"})
    if mode == "ok_false":
        return httpx.Response(200, json={"ok": False, "description": "bad request"})
    raise ValueError(mode)


def _assert_send_failure_summary(s) -> None:
    assert s.iterations_requested == 1
    assert s.iterations_completed == 1
    assert s.poll_once_exception_count == 0
    assert s.send_failure_count == 1
    assert s.fetch_failure_count == 0
    assert s.processing_failure_count == 0
    assert s.send_count == 0
    assert s.received_count == 1
    assert s.noop_count == 0


def _assert_fetch_failure_summary(s) -> None:
    assert s.iterations_requested == 1
    assert s.iterations_completed == 1
    assert s.poll_once_exception_count == 0
    assert s.fetch_failure_count == 1
    assert s.send_failure_count == 0
    assert s.processing_failure_count == 0
    assert s.send_count == 0
    assert s.received_count == 0
    assert s.noop_count == 0


def _patch_capture_bundle(captured: list[Slice1HttpxLiveRuntimeBundle]):
    def _wrap(*args, **kwargs):
        b = _orig_build(*args, **kwargs)
        captured.append(b)
        return b

    return patch.object(
        runner_mod,
        "build_slice1_httpx_live_runtime_bundle",
        side_effect=_wrap,
    )


@pytest.mark.parametrize("send_fail_mode", ["http_error", "ok_false"])
def test_runner_send_fails_counters_offset_audit(send_fail_mode: str) -> None:
    uid = 13
    u = _start_update(update_id=uid)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [u]})
        if request.url.path.endswith("/sendMessage"):
            return _send_fail_response(send_fail_mode)
        return httpx.Response(404)

    captured: list[Slice1HttpxLiveRuntimeBundle] = []

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with _patch_capture_bundle(captured):
                s = await run_slice1_httpx_live_iterations(
                    "t",
                    1,
                    client=ac,
                    correlation_id=new_correlation_id(),
                )
            _assert_send_failure_summary(s)
            assert len(captured) == 1
            assert captured[0].bundle.runtime.current_offset == uid + 1
            assert len(await captured[0].bundle.composition.audit.recorded_events()) == 1

    _run(main())


def test_runner_send_failure_is_not_fetch_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [_start_update(update_id=3)]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(503, json={"ok": False})
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            s = await run_slice1_httpx_live_iterations(
                "t",
                1,
                client=ac,
                correlation_id=new_correlation_id(),
            )
            assert s.fetch_failure_count == 0 and s.send_failure_count == 1

    _run(main())


@pytest.mark.parametrize("fetch_fail_mode", ["http_error", "ok_false"])
def test_runner_getupdates_fails_counters_no_send_no_audit_no_offset(
    fetch_fail_mode: str,
) -> None:
    send_hits: list[None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return _fetch_fail_response(fetch_fail_mode)
        if request.url.path.endswith("/sendMessage"):
            send_hits.append(None)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    captured: list[Slice1HttpxLiveRuntimeBundle] = []

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with _patch_capture_bundle(captured):
                s = await run_slice1_httpx_live_iterations(
                    "t",
                    1,
                    client=ac,
                    correlation_id=new_correlation_id(),
                )
            _assert_fetch_failure_summary(s)
            assert not send_hits
            assert len(captured) == 1
            assert captured[0].bundle.runtime.current_offset is None
            assert len(await captured[0].bundle.composition.audit.recorded_events()) == 0

    _run(main())


@pytest.mark.parametrize("fetch_fail_mode", ["http_error", "ok_false"])
def test_runner_offset_in_json_after_success_then_fetch_failure(fetch_fail_mode: str) -> None:
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

    captured: list[Slice1HttpxLiveRuntimeBundle] = []

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with _patch_capture_bundle(captured):
                s = await run_slice1_httpx_live_iterations(
                    "t",
                    2,
                    client=ac,
                    correlation_id=new_correlation_id(),
                )
            assert s.fetch_failure_count == 1
            assert s.send_failure_count == 0
            assert s.received_count == 1
            assert len(get_updates_bodies) == 2
            assert get_updates_bodies[1].get("offset") == 8
            assert len(captured) == 1
            assert captured[0].bundle.runtime.current_offset == 8
            assert len(await captured[0].bundle.composition.audit.recorded_events()) == 1

    _run(main())
