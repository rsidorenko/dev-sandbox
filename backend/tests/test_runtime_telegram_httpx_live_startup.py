"""Wiring tests for :mod:`app.runtime.telegram_httpx_live_startup` (no network)."""

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import AsyncMock, patch

import httpx

import app.runtime as rt
import app.runtime.telegram_httpx_live_startup as httpx_live_mod
from app.runtime import accept_mapping_runtime_update
from app.runtime.live_startup import Slice1InMemoryLiveRawRuntimeBundle
from app.runtime.polling import PollingRuntimeConfig
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
from app.runtime.telegram_httpx_live_startup import (
    Slice1HttpxLiveRuntimeBundle,
    build_slice1_httpx_live_runtime_bundle,
)
from app.runtime.telegram_httpx_raw_client import HttpxTelegramRawPollingClient
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


def _mock_transport_start_ok() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [_start_update()]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


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


def test_builder_returns_httpx_client_and_live_raw_bundle() -> None:
    async def main() -> None:
        transport = _mock_transport_start_ok()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_live_runtime_bundle("dummy-token", client=ac)
            assert isinstance(b, Slice1HttpxLiveRuntimeBundle)
            assert isinstance(b.client, HttpxTelegramRawPollingClient)
            assert isinstance(b.bundle, Slice1InMemoryLiveRawRuntimeBundle)
            await b.aclose()

    asyncio.run(main())


def test_config_none_uses_default_polling_config() -> None:
    async def main() -> None:
        transport = _mock_transport_start_ok()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_live_runtime_bundle("dummy-token", client=ac)
            assert b.bundle.config == PollingRuntimeConfig()
            await b.aclose()

    asyncio.run(main())


def test_default_polling_policy_is_module_default() -> None:
    async def main() -> None:
        transport = _mock_transport_start_ok()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_live_runtime_bundle("dummy-token", client=ac)
            assert b.client.polling_policy is DEFAULT_POLLING_POLICY
            await b.aclose()

    asyncio.run(main())


def test_custom_polling_policy_reaches_client_by_identity() -> None:
    custom = PollingPolicy(
        timeout=NoopTimeoutPolicy(),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        transport = _mock_transport_start_ok()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_live_runtime_bundle("dummy-token", client=ac, polling_policy=custom)
            assert b.client.polling_policy is custom
            await b.aclose()

    asyncio.run(main())


def test_custom_config_reaches_inner_bundle() -> None:
    cfg = PollingRuntimeConfig(max_updates_per_batch=2)

    async def main() -> None:
        transport = _mock_transport_start_ok()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_live_runtime_bundle("dummy-token", client=ac, config=cfg)
            assert b.bundle.config is cfg
            assert b.bundle.config.max_updates_per_batch == 2
            await b.aclose()

    asyncio.run(main())


def test_default_bridge_on_inner_bundle() -> None:
    async def main() -> None:
        transport = _mock_transport_start_ok()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_live_runtime_bundle("dummy-token", client=ac)
            assert b.bundle.bridge is accept_mapping_runtime_update
            await b.aclose()

    asyncio.run(main())


def test_override_httpx_timeout_mode_public_startup_path_reaches_get_updates_post() -> None:
    expected_timeout = httpx.Timeout(37.5, connect=3.0)
    timeout_policy = _RecordingOverrideTimeoutPolicy(expected_timeout)
    polling_policy = PollingPolicy(
        timeout=timeout_policy,
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )
    fake = _RecordingFakeAsyncClient()

    async def main() -> None:
        b = build_slice1_httpx_live_runtime_bundle(
            "t",
            client=cast("httpx.AsyncClient", fake),
            polling_policy=polling_policy,
        )
        summary = await b.bundle.live_loop.run_until_stopped(
            b.bundle.control,
            correlation_id=new_correlation_id(),
            max_iterations=1,
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
        await b.aclose()

    asyncio.run(main())


def test_one_iteration_start_one_send() -> None:
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
            b = build_slice1_httpx_live_runtime_bundle("t", client=ac)
            summary = await b.bundle.live_loop.run_until_stopped(
                b.bundle.control,
                correlation_id=new_correlation_id(),
                max_iterations=1,
            )
            assert summary.send_count == 1
            assert send_posts == 1
            await b.aclose()

    asyncio.run(main())


def test_two_iterations_same_start_replay_second_noop_one_audit() -> None:
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
            b = build_slice1_httpx_live_runtime_bundle("t", client=ac)
            ctrl = b.bundle.control
            cid = new_correlation_id()
            s1 = await b.bundle.live_loop.run_until_stopped(ctrl, correlation_id=cid, max_iterations=1)
            s2 = await b.bundle.live_loop.run_until_stopped(ctrl, correlation_id=cid, max_iterations=1)
            assert s1.send_count == 1 and s1.noop_count == 0
            assert s2.send_count == 0 and s2.noop_count == 1
            assert send_posts == 1
            events = await b.bundle.composition.audit.recorded_events()
            assert len(events) == 1
            await b.aclose()

    asyncio.run(main())


def test_wrapper_aclose_idempotent() -> None:
    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_live_runtime_bundle("t", client=ac)
            await b.aclose()
            await b.aclose()

    asyncio.run(main())


def test_owned_client_aclose_once_via_public_live_builder() -> None:
    owned = SimpleNamespace(aclose=AsyncMock())

    async def main() -> None:
        with patch("app.runtime.telegram_httpx_raw_client.httpx.AsyncClient", return_value=owned) as ctor:
            b = build_slice1_httpx_live_runtime_bundle("dummy-token")
            assert isinstance(b, Slice1HttpxLiveRuntimeBundle)
            await b.aclose()
            await b.aclose()
            ctor.assert_called_once()
            owned.aclose.assert_awaited_once()

    asyncio.run(main())


def test_app_runtime_exports_httpx_live_symbols() -> None:
    assert hasattr(rt, "Slice1HttpxLiveRuntimeBundle")
    assert hasattr(rt, "build_slice1_httpx_live_runtime_bundle")
    assert "Slice1HttpxLiveRuntimeBundle" in rt.__all__
    assert "build_slice1_httpx_live_runtime_bundle" in rt.__all__
    assert rt.Slice1HttpxLiveRuntimeBundle is Slice1HttpxLiveRuntimeBundle
    assert rt.build_slice1_httpx_live_runtime_bundle is build_slice1_httpx_live_runtime_bundle


def test_module_source_excludes_forbidden_tokens() -> None:
    src = inspect.getsource(httpx_live_mod)
    lower = src.lower()
    for token in ("billing", "issuance", "admin", "webhook"):
        assert token not in lower


def test_module_source_no_env_cli_signal_sleep_backoff() -> None:
    src = inspect.getsource(httpx_live_mod)
    lower = src.lower()
    for token in ("environ", "getenv", "dotenv", "argparse", "click", "signal", "sleep", "backoff"):
        assert token not in lower
