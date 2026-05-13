"""Tests for :mod:`app.runtime.telegram_httpx_live_env_loop` (no network, no real env reads)."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Literal
from unittest.mock import patch

import httpx
import pytest

import app.runtime as rt
import app.runtime.telegram_httpx_live_env as env_mod
import app.runtime.telegram_httpx_live_env_loop as env_loop_mod
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
from app.runtime.telegram_httpx_live_app import Slice1HttpxLiveRuntimeApp
from app.runtime.telegram_httpx_live_env import (
    build_slice1_httpx_live_runtime_app_from_env,
    build_slice1_httpx_live_runtime_app_from_env_async,
)
from app.security.config import RuntimeConfig
from app.shared.correlation import new_correlation_id


def _run(coro):
    return asyncio.run(coro)


def _minimal_runtime_config(*, bot_token: str = "1234567890tok") -> RuntimeConfig:
    return RuntimeConfig(
        bot_token=bot_token,
        database_url="postgresql://localhost/db",
        app_env="development",
        debug_safe=False,
    )


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


def test_helper_returns_polling_run_summary() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                summary = await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                    LoopControl(),
                    client=ac,
                    max_iterations=1,
                )
            assert isinstance(summary, PollingRunSummary)

    _run(main())


def test_override_httpx_timeout_mode_passes_through_env_helper_to_getupdates_post() -> None:
    cfg = _minimal_runtime_config()
    expected_timeout = httpx.Timeout(42.0, connect=5.0)
    polling_policy = PollingPolicy(
        timeout=_FixedOverrideTimeoutPolicy(expected_timeout),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        fake = _RecordingFakeAsyncClient()
        with patch.object(env_mod, "load_runtime_config", return_value=cfg):
            summary = await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                LoopControl(),
                client=fake,
                polling_policy=polling_policy,
                max_iterations=1,
            )
        assert len(fake.post_calls) == 1
        url, body, kw = fake.post_calls[0]
        assert url.endswith("getUpdates")
        assert body == {"limit": 100, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
        assert "timeout" in kw
        assert kw["timeout"] is expected_timeout
        assert summary.fetch_failure_count == 0
        assert summary.send_failure_count == 0

    _run(main())


def test_public_runtime_entrypoint_override_timeout_env_loop_getupdates_post_identity() -> None:
    """Direct public :func:`run_slice1_httpx_live_until_stopped_from_env` (``app.runtime``) path."""
    cfg = _minimal_runtime_config()
    expected_timeout = httpx.Timeout(43.0, connect=4.0)
    timeout_policy = _RecordingOverrideTimeoutPolicy(expected_timeout)
    polling_policy = PollingPolicy(
        timeout=timeout_policy,
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        fake = _RecordingFakeAsyncClient()
        with patch.object(env_mod, "load_runtime_config", return_value=cfg):
            summary = await rt.run_slice1_httpx_live_until_stopped_from_env(
                LoopControl(),
                client=fake,
                polling_policy=polling_policy,
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

    _run(main())


def test_uses_build_from_env() -> None:
    cfg = _minimal_runtime_config()
    calls: list[dict] = []

    async def spy(**kwargs):
        calls.append(kwargs)
        return await build_slice1_httpx_live_runtime_app_from_env_async(**kwargs)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with patch.object(
                    env_loop_mod,
                    "build_slice1_httpx_live_runtime_app_from_env_async",
                    side_effect=spy,
                ):
                    await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                        LoopControl(),
                        client=ac,
                        max_iterations=0,
                    )
            assert len(calls) == 1
            assert calls[0]["client"] is ac
            assert calls[0]["polling_policy"] is DEFAULT_POLLING_POLICY

    _run(main())


def test_custom_polling_policy_identity_on_app_client() -> None:
    cfg = _minimal_runtime_config()
    custom = PollingPolicy(
        timeout=NoopTimeoutPolicy(),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )
    captured: list[PollingPolicy] = []

    async def spy(**kwargs):
        app = await build_slice1_httpx_live_runtime_app_from_env_async(**kwargs)
        captured.append(app.bundle.client.polling_policy)
        return app

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with patch.object(
                    env_loop_mod,
                    "build_slice1_httpx_live_runtime_app_from_env_async",
                    side_effect=spy,
                ):
                    await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                        LoopControl(),
                        client=ac,
                        max_iterations=0,
                        polling_policy=custom,
                    )
            assert len(captured) == 1
            assert captured[0] is custom

    _run(main())


def test_stop_requested_before_start_empty_summary() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                summary = await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                    LoopControl(stop_requested=True),
                    client=ac,
                )
            assert summary == _empty_summary()

    _run(main())


def test_one_start_max_iterations_one_send_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [_start_update()]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                summary = await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                    LoopControl(),
                    client=ac,
                    correlation_id=new_correlation_id(),
                    max_iterations=1,
                )
            assert summary.send_count == 1

    _run(main())


def test_external_loop_control_propagated(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _minimal_runtime_config()
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
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                probe = build_slice1_httpx_live_runtime_app_from_env(client=ac)
                internal = probe.bundle.bundle.control
                external = LoopControl()
                try:
                    await env_loop_mod.run_slice1_httpx_live_until_stopped_from_env(
                        external,
                        client=ac,
                    )
                finally:
                    await probe.aclose()
            assert len(captured) == 1
            assert captured[0] is external
            assert captured[0] is not internal

    _run(main())


def test_aclose_when_run_until_stopped_raises(monkeypatch: pytest.MonkeyPatch) -> None:
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
    ) -> PollingRunSummary:
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


def test_app_runtime_exports_helper() -> None:
    from app.runtime import run_slice1_httpx_live_until_stopped_from_env

    assert rt.run_slice1_httpx_live_until_stopped_from_env is run_slice1_httpx_live_until_stopped_from_env
    assert "run_slice1_httpx_live_until_stopped_from_env" in rt.__all__


def test_module_source_excludes_forbidden_tokens() -> None:
    src = inspect.getsource(env_loop_mod)
    lower = src.lower()
    for token in ("billing", "issuance", "admin", "webhook"):
        assert token not in lower


def test_module_source_no_manual_env_cli_signal_sleep_backoff() -> None:
    src = inspect.getsource(env_loop_mod)
    lower = src.lower()
    for token in ("environ", "getenv", "dotenv", "argparse", "click", "signal", "sleep", "backoff"):
        assert token not in lower
