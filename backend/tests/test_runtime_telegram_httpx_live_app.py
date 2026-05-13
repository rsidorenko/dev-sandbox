"""Tests for :mod:`app.runtime.telegram_httpx_live_app` (no network)."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Literal, cast

import httpx
import pytest

import app.runtime as rt
import app.runtime.telegram_httpx_live_app as httpx_live_app_mod
from app.runtime.live_loop import LoopControl, Slice1LiveRawPollingLoop
from app.runtime.polling_policy import (
    DEFAULT_POLLING_POLICY,
    LONG_POLL_FETCH_REQUEST,
    NoopBackoffPolicy,
    NoopRetryPolicy,
    NoopTimeoutPolicy,
    OVERRIDE_HTTPX_TIMEOUT_MODE,
    PollingPolicy,
    PollingTimeoutDecision,
    RequestKind,
)
from app.runtime.runner import PollingRunSummary
from app.runtime.telegram_httpx_live_app import (
    Slice1HttpxLiveRuntimeApp,
    build_slice1_httpx_live_runtime_app,
)
from app.shared.correlation import new_correlation_id


def _json_body(request: httpx.Request) -> dict:
    if not request.content:
        return {}
    return json.loads(request.content.decode())


def _start_update(*, update_id: int = 1, user_id: int = 42) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": user_id, "is_bot": False, "first_name": "U"},
            "chat": {"id": user_id, "type": "private"},
            "text": "/start",
        },
    }


def _empty_summary() -> PollingRunSummary:
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


class _RecordingOverrideTimeoutPolicy:
    __slots__ = ("httpx_timeout", "decisions")
    kind: Literal["noop"] = "noop"

    def __init__(self, httpx_timeout: httpx.Timeout) -> None:
        self.httpx_timeout = httpx_timeout
        self.decisions: list[PollingTimeoutDecision] = []

    def timeout_for_request(self, request_kind: RequestKind) -> PollingTimeoutDecision:
        d = PollingTimeoutDecision(
            request_kind=request_kind,
            mode=OVERRIDE_HTTPX_TIMEOUT_MODE,
            httpx_timeout=self.httpx_timeout,
        )
        self.decisions.append(d)
        return d


class _RecordingFakeAsyncClient:
    __slots__ = ("post_calls",)

    def __init__(self) -> None:
        self.post_calls: list[tuple[str, object, dict[str, object]]] = []

    async def post(self, url: str, *, json: object | None = None, **kwargs: object) -> httpx.Response:
        self.post_calls.append((url, json, kwargs))
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"ok": True, "result": []}, request=req)


def test_factory_returns_app() -> None:
    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app("t", client=ac)
            assert isinstance(app, Slice1HttpxLiveRuntimeApp)
            assert app.bundle.client.polling_policy is DEFAULT_POLLING_POLICY
            await app.aclose()

    asyncio.run(main())


def test_factory_passes_custom_polling_policy_by_identity() -> None:
    custom = PollingPolicy(
        timeout=NoopTimeoutPolicy(),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app("t", client=ac, polling_policy=custom)
            assert app.bundle.client.polling_policy is custom
            await app.aclose()

    asyncio.run(main())


def test_run_iterations_zero_empty_summary() -> None:
    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": [_start_update()]}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app("t", client=ac)
            summary = await app.run_iterations(0)
            assert summary == _empty_summary()
            await app.aclose()

    asyncio.run(main())


def test_override_httpx_timeout_mode_public_app_path_reaches_get_updates_post() -> None:
    expected_timeout = httpx.Timeout(37.5, connect=3.0)
    timeout_policy = _RecordingOverrideTimeoutPolicy(expected_timeout)
    polling_policy = PollingPolicy(
        timeout=timeout_policy,
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )
    fake = _RecordingFakeAsyncClient()

    async def main() -> None:
        app = build_slice1_httpx_live_runtime_app(
            "t",
            client=cast(httpx.AsyncClient, fake),
            polling_policy=polling_policy,
        )
        summary = await app.run_iterations(1)
        assert len(timeout_policy.decisions) == 1
        d0 = timeout_policy.decisions[0]
        assert d0.request_kind == LONG_POLL_FETCH_REQUEST
        assert d0.mode == OVERRIDE_HTTPX_TIMEOUT_MODE
        assert d0.httpx_timeout is expected_timeout
        assert len(fake.post_calls) == 1
        url, body, kw = fake.post_calls[0]
        assert url.endswith("getUpdates")
        assert body == {"limit": 100, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
        assert kw["timeout"] is expected_timeout
        assert summary.fetch_failure_count == 0
        assert summary.send_failure_count == 0
        await app.aclose()

    asyncio.run(main())


def test_run_iterations_one_start_one_send() -> None:
    send_posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal send_posts
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [_start_update()]})
        if request.url.path.endswith("/sendMessage"):
            send_posts += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app("t", client=ac)
            summary = await app.run_iterations(1, correlation_id=new_correlation_id())
            assert summary.send_count == 1
            assert send_posts == 1
            await app.aclose()

    asyncio.run(main())


def test_run_until_stopped_uses_external_control(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[LoopControl] = []

    async def capture_run(
        self: Slice1LiveRawPollingLoop,
        ctl: LoopControl,
        *,
        correlation_id: str | None = None,
        max_iterations: int | None = None,
    ) -> PollingRunSummary:
        captured.append(ctl)
        return _empty_summary()

    monkeypatch.setattr(Slice1LiveRawPollingLoop, "run_until_stopped", capture_run)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app("t", client=ac)
            external = LoopControl()
            try:
                await app.run_until_stopped(external)
            finally:
                await app.aclose()
            assert len(captured) == 1
            assert captured[0] is external
            assert captured[0] is not app.bundle.bundle.control

    asyncio.run(main())


def test_same_app_twice_same_update_replay_second_noop_one_audit() -> None:
    send_posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal send_posts
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [_start_update()]})
        if request.url.path.endswith("/sendMessage"):
            send_posts += 1
            body = _json_body(request)
            assert body.get("chat_id") == 42
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app("t", client=ac)
            cid = new_correlation_id()
            s1 = await app.run_iterations(1, correlation_id=cid)
            s2 = await app.run_iterations(1, correlation_id=cid)
            assert s1.send_count == 1 and s1.noop_count == 0
            assert s2.send_count == 0 and s2.noop_count == 1
            assert send_posts == 1
            events = await app.bundle.bundle.composition.audit.recorded_events()
            assert len(events) == 1
            await app.aclose()

    asyncio.run(main())


def test_aclose_idempotent() -> None:
    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app("t", client=ac)
            await app.aclose()
            await app.aclose()

    asyncio.run(main())


def test_app_runtime_exports() -> None:
    assert rt.Slice1HttpxLiveRuntimeApp is Slice1HttpxLiveRuntimeApp
    assert rt.build_slice1_httpx_live_runtime_app is build_slice1_httpx_live_runtime_app
    assert "Slice1HttpxLiveRuntimeApp" in rt.__all__
    assert "build_slice1_httpx_live_runtime_app" in rt.__all__


def test_module_source_excludes_forbidden_tokens() -> None:
    src = inspect.getsource(httpx_live_app_mod)
    lower = src.lower()
    for token in ("billing", "issuance", "admin", "webhook"):
        assert token not in lower


def test_module_source_no_env_cli_signal_sleep_backoff() -> None:
    src = inspect.getsource(httpx_live_app_mod)
    lower = src.lower()
    for token in ("environ", "getenv", "dotenv", "argparse", "click", "signal", "sleep", "backoff"):
        assert token not in lower
