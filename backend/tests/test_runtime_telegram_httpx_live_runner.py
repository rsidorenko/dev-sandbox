"""Tests for :func:`app.runtime.telegram_httpx_live_runner.run_slice1_httpx_live_iterations`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import app.runtime as rt
import app.runtime.telegram_httpx_live_runner as runner_mod
from app.runtime.live_loop import Slice1LiveRawPollingLoop
from app.runtime.polling_policy import (
    DEFAULT_POLLING_POLICY,
    LONG_POLL_FETCH_REQUEST,
    OVERRIDE_HTTPX_TIMEOUT_MODE,
    NoopBackoffPolicy,
    NoopRetryPolicy,
    NoopTimeoutPolicy,
    PollingPolicy,
    PollingTimeoutDecision,
    RequestKind,
)
from app.runtime.runner import PollingRunSummary
from app.runtime.telegram_httpx_live_runner import run_slice1_httpx_live_iterations
from app.runtime.telegram_httpx_live_startup import build_slice1_httpx_live_runtime_bundle as _orig_build
from app.runtime.telegram_httpx_raw_client import HttpxTelegramRawPollingClient


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


def _mock_transport_start_ok() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [_start_update()]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _empty_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "result": []}))


@dataclass(frozen=True, slots=True)
class _FixedOverrideTimeoutPolicy:
    httpx_timeout: httpx.Timeout
    kind: Literal["noop"] = "noop"

    def timeout_for_request(self, request_kind: RequestKind) -> PollingTimeoutDecision:
        return PollingTimeoutDecision(
            request_kind=request_kind,
            mode=OVERRIDE_HTTPX_TIMEOUT_MODE,
            httpx_timeout=self.httpx_timeout,
        )


class _RecordingOverrideTimeoutPolicy:
    __slots__ = ("decisions", "httpx_timeout")
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


def test_default_polling_policy_passed_to_build() -> None:
    orig = runner_mod.build_slice1_httpx_live_runtime_bundle
    captured: dict[str, object] = {}

    def spy(*a, **kw):
        captured.clear()
        captured.update(kw)
        return orig(*a, **kw)

    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(
                runner_mod,
                "build_slice1_httpx_live_runtime_bundle",
                side_effect=spy,
            ):
                await run_slice1_httpx_live_iterations("t", 0, client=ac)
        assert captured["polling_policy"] is DEFAULT_POLLING_POLICY

    asyncio.run(main())


def test_custom_polling_policy_reaches_bundle_client() -> None:
    custom = PollingPolicy(
        timeout=NoopTimeoutPolicy(),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )
    built: list = []
    orig = runner_mod.build_slice1_httpx_live_runtime_bundle

    def spy(*a, **kw):
        b = orig(*a, **kw)
        built.append(b)
        return b

    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(
                runner_mod,
                "build_slice1_httpx_live_runtime_bundle",
                side_effect=spy,
            ):
                await run_slice1_httpx_live_iterations("t", 0, client=ac, polling_policy=custom)
        assert len(built) == 1
        assert built[0].client.polling_policy is custom

    asyncio.run(main())


def test_override_httpx_timeout_mode_passes_through_helper_to_get_updates_post() -> None:
    expected_timeout = httpx.Timeout(37.5, connect=3.0)
    polling_policy = PollingPolicy(
        timeout=_FixedOverrideTimeoutPolicy(httpx_timeout=expected_timeout),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )
    fake = _RecordingFakeAsyncClient()

    async def main() -> None:
        summary = await run_slice1_httpx_live_iterations(
            "t",
            1,
            client=cast("httpx.AsyncClient", fake),
            polling_policy=polling_policy,
        )
        assert len(fake.post_calls) == 1
        url, body, kw = fake.post_calls[0]
        assert url.endswith("getUpdates")
        assert body == {"limit": 100, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
        assert "timeout" in kw
        assert kw["timeout"] is expected_timeout
        assert summary.fetch_failure_count == 0
        assert summary.send_failure_count == 0

    asyncio.run(main())


def test_override_httpx_timeout_mode_direct_public_runner_path_reaches_getupdates_post() -> None:
    expected_timeout = httpx.Timeout(42.0, connect=5.0)
    timeout_policy = _RecordingOverrideTimeoutPolicy(expected_timeout)
    polling_policy = PollingPolicy(
        timeout=timeout_policy,
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        fake = _RecordingFakeAsyncClient()
        summary = await rt.run_slice1_httpx_live_iterations(
            "t",
            1,
            client=cast("httpx.AsyncClient", fake),
            polling_policy=polling_policy,
        )
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

    asyncio.run(main())


def test_returns_polling_run_summary() -> None:
    async def main() -> None:
        transport = _mock_transport_start_ok()
        async with httpx.AsyncClient(transport=transport) as ac:
            summary = await run_slice1_httpx_live_iterations("t", 1, client=ac)
        assert isinstance(summary, PollingRunSummary)

    asyncio.run(main())


def test_iterations_zero_empty_summary() -> None:
    async def main() -> None:
        transport = _mock_transport_start_ok()
        async with httpx.AsyncClient(transport=transport) as ac:
            summary = await run_slice1_httpx_live_iterations("t", 0, client=ac)
        assert summary.iterations_completed == 0
        assert summary.send_count == 0
        assert summary.received_count == 0

    asyncio.run(main())


def test_one_start_one_send() -> None:
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
            summary = await run_slice1_httpx_live_iterations("t", 1, client=ac)
        assert summary.send_count == 1
        assert send_posts == 1

    asyncio.run(main())


def test_each_helper_call_builds_new_bundle_audit_not_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list = []

    def tracking(*args, **kwargs):
        b = _orig_build(*args, **kwargs)
        built.append(b)
        return b

    monkeypatch.setattr(runner_mod, "build_slice1_httpx_live_runtime_bundle", tracking)

    async def main() -> None:
        transport = _mock_transport_start_ok()
        async with httpx.AsyncClient(transport=transport) as ac:
            await run_slice1_httpx_live_iterations("t", 1, client=ac)
            await run_slice1_httpx_live_iterations("t", 1, client=ac)

        assert len(built) == 2
        assert built[0] is not built[1]
        e0 = await built[0].bundle.composition.audit.recorded_events()
        e1 = await built[1].bundle.composition.audit.recorded_events()
        assert len(e0) == 1
        assert len(e1) == 1

    asyncio.run(main())


def test_aclose_runs_when_run_until_stopped_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    aclose_calls = 0
    orig_aclose = HttpxTelegramRawPollingClient.aclose

    async def counting_aclose(self: HttpxTelegramRawPollingClient) -> None:
        nonlocal aclose_calls
        aclose_calls += 1
        await orig_aclose(self)

    monkeypatch.setattr(HttpxTelegramRawPollingClient, "aclose", counting_aclose)
    monkeypatch.setattr(
        Slice1LiveRawPollingLoop,
        "run_until_stopped",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            try:
                await run_slice1_httpx_live_iterations("t", 1, client=ac)
            except RuntimeError as exc:
                assert str(exc) == "boom"
            else:
                raise AssertionError("expected RuntimeError")

        assert aclose_calls == 1

    asyncio.run(main())
