"""Tests for :func:`app.runtime.telegram_httpx_live_env_runner.run_slice1_httpx_live_iterations_from_env`."""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import app.runtime as rt
import app.runtime.telegram_httpx_live_env as env_mod
import app.runtime.telegram_httpx_live_env_runner as env_runner_mod
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
from app.runtime.runner import PollingRunSummary
from app.runtime.telegram_httpx_live_app import Slice1HttpxLiveRuntimeApp
from app.runtime.telegram_httpx_live_env import (
    build_slice1_httpx_live_runtime_app_from_env_async,
)
from app.runtime.telegram_httpx_live_env_runner import run_slice1_httpx_live_iterations_from_env
from app.security.config import RuntimeConfig
from app.shared.correlation import new_correlation_id


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


def test_returns_polling_run_summary() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                summary = await run_slice1_httpx_live_iterations_from_env(1, client=ac)
        assert isinstance(summary, PollingRunSummary)

    asyncio.run(main())


def test_build_from_env_called() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with (
                patch.object(env_mod, "load_runtime_config", return_value=cfg),
                patch.object(
                    env_runner_mod,
                    "build_slice1_httpx_live_runtime_app_from_env_async",
                    wraps=build_slice1_httpx_live_runtime_app_from_env_async,
                ) as spy,
            ):
                await run_slice1_httpx_live_iterations_from_env(0, client=ac)
            spy.assert_called_once_with(
                polling_config=None,
                base_url=None,
                client=ac,
                polling_policy=DEFAULT_POLLING_POLICY,
            )

    asyncio.run(main())


def test_default_polling_policy_passed_to_build() -> None:
    cfg = _minimal_runtime_config()
    orig = env_mod.build_slice1_httpx_live_runtime_app_from_env_async
    captured: dict[str, object] = {}

    async def spy(*a, **kw):
        captured.clear()
        captured.update(kw)
        return await orig(*a, **kw)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with patch.object(
                    env_runner_mod,
                    "build_slice1_httpx_live_runtime_app_from_env_async",
                    side_effect=spy,
                ):
                    await run_slice1_httpx_live_iterations_from_env(0, client=ac)
        assert captured["polling_policy"] is DEFAULT_POLLING_POLICY

    asyncio.run(main())


def test_custom_polling_policy_reaches_bundle_client() -> None:
    cfg = _minimal_runtime_config()
    custom = PollingPolicy(
        timeout=NoopTimeoutPolicy(),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )
    built: list = []
    orig = env_mod.build_slice1_httpx_live_runtime_app_from_env_async

    async def spy(*a, **kw):
        app = await orig(*a, **kw)
        built.append(app)
        return app

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with patch.object(
                    env_runner_mod,
                    "build_slice1_httpx_live_runtime_app_from_env_async",
                    side_effect=spy,
                ):
                    await run_slice1_httpx_live_iterations_from_env(0, client=ac, polling_policy=custom)
        assert len(built) == 1
        assert built[0].bundle.client.polling_policy is custom

    asyncio.run(main())


class _OverrideAllTimeoutPolicy:
    """Returns OVERRIDE_HTTPX_TIMEOUT_MODE with a fixed :class:`httpx.Timeout` for every request kind."""

    kind: str = "test_override"

    def __init__(self, httpx_timeout: httpx.Timeout) -> None:
        self._httpx_timeout = httpx_timeout

    def timeout_for_request(self, request_kind: RequestKind) -> PollingTimeoutDecision:
        return PollingTimeoutDecision(
            request_kind=request_kind,
            mode=OVERRIDE_HTTPX_TIMEOUT_MODE,
            httpx_timeout=self._httpx_timeout,
        )


class _RecordingOverrideTimeoutPolicy:
    __slots__ = ("decisions", "httpx_timeout")
    kind: str = "test_override"

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
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict[str, object]]] = []

    async def post(self, url: str, *, json: object | None = None, **kwargs: object) -> httpx.Response:
        self.calls.append((url, json, dict(kwargs)))
        return httpx.Response(
            200,
            json={"ok": True, "result": []},
            request=httpx.Request("POST", url),
        )


def test_override_httpx_timeout_mode_reaches_getupdates_post() -> None:
    cfg = _minimal_runtime_config()
    expected_timeout = httpx.Timeout(12.34)
    polling_policy = PollingPolicy(
        timeout=_OverrideAllTimeoutPolicy(expected_timeout),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        fake = _RecordingFakeAsyncClient()
        with patch.object(env_mod, "load_runtime_config", return_value=cfg):
            summary = await run_slice1_httpx_live_iterations_from_env(
                1,
                client=fake,
                polling_policy=polling_policy,
            )
        assert len(fake.calls) == 1
        url, body, kw = fake.calls[0]
        assert url.endswith("getUpdates")
        assert body == {"limit": 100, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
        assert "timeout" in kw
        assert kw["timeout"] is expected_timeout
        assert summary.fetch_failure_count == 0
        assert summary.send_failure_count == 0

    asyncio.run(main())


def test_public_runtime_entrypoint_override_timeout_env_runner_getupdates_post_identity() -> None:
    """Direct public :func:`run_slice1_httpx_live_iterations_from_env` (``app.runtime``) path."""
    cfg = _minimal_runtime_config()
    expected_timeout = httpx.Timeout(44.0, connect=6.0)
    timeout_policy = _RecordingOverrideTimeoutPolicy(expected_timeout)
    polling_policy = PollingPolicy(
        timeout=timeout_policy,
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        fake = _RecordingFakeAsyncClient()
        with patch.object(env_mod, "load_runtime_config", return_value=cfg):
            summary = await rt.run_slice1_httpx_live_iterations_from_env(
                1,
                client=fake,
                polling_policy=polling_policy,
            )
        assert len(timeout_policy.decisions) == 1
        d0 = timeout_policy.decisions[0]
        assert d0.request_kind == LONG_POLL_FETCH_REQUEST
        assert d0.mode == OVERRIDE_HTTPX_TIMEOUT_MODE
        assert d0.httpx_timeout is expected_timeout
        assert len(fake.calls) == 1
        url, body, kw = fake.calls[0]
        assert url.endswith("getUpdates")
        assert body == {"limit": 100, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
        assert kw["timeout"] is expected_timeout
        assert summary.fetch_failure_count == 0
        assert summary.send_failure_count == 0

    asyncio.run(main())


def test_iterations_zero_empty_summary() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                summary = await run_slice1_httpx_live_iterations_from_env(0, client=ac)
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

    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                summary = await run_slice1_httpx_live_iterations_from_env(
                    1,
                    client=ac,
                    correlation_id=new_correlation_id(),
                )
        assert summary.send_count == 1
        assert send_posts == 1

    asyncio.run(main())


def test_aclose_when_run_iterations_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _minimal_runtime_config()
    aclose_calls = 0
    orig_aclose = Slice1HttpxLiveRuntimeApp.aclose

    async def counting_aclose(self: Slice1HttpxLiveRuntimeApp) -> None:
        nonlocal aclose_calls
        aclose_calls += 1
        await orig_aclose(self)

    monkeypatch.setattr(Slice1HttpxLiveRuntimeApp, "aclose", counting_aclose)
    monkeypatch.setattr(
        Slice1HttpxLiveRuntimeApp,
        "run_iterations",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                try:
                    await run_slice1_httpx_live_iterations_from_env(1, client=ac)
                except RuntimeError as exc:
                    assert str(exc) == "boom"
                else:
                    raise AssertionError("expected RuntimeError")

        assert aclose_calls == 1

    asyncio.run(main())


def test_app_runtime_import() -> None:
    assert rt.run_slice1_httpx_live_iterations_from_env is run_slice1_httpx_live_iterations_from_env
    assert "run_slice1_httpx_live_iterations_from_env" in rt.__all__


def test_module_source_excludes_forbidden_tokens() -> None:
    src = inspect.getsource(env_runner_mod)
    lower = src.lower()
    for token in ("billing", "issuance", "admin", "webhook"):
        assert token not in lower


def test_module_source_no_manual_env_cli_signal_sleep_backoff() -> None:
    src = inspect.getsource(env_runner_mod)
    lower = src.lower()
    for token in ("environ", "getenv", "dotenv", "argparse", "click", "signal", "sleep", "backoff"):
        assert token not in lower


def test_optional_polling_config_passed_to_build() -> None:
    cfg = _minimal_runtime_config()
    custom = PollingRuntimeConfig(max_updates_per_batch=5)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with (
                patch.object(env_mod, "load_runtime_config", return_value=cfg),
                patch.object(
                    env_runner_mod,
                    "build_slice1_httpx_live_runtime_app_from_env_async",
                    wraps=build_slice1_httpx_live_runtime_app_from_env_async,
                ) as spy,
            ):
                await run_slice1_httpx_live_iterations_from_env(0, polling_config=custom, client=ac)
            spy.assert_called_once_with(
                polling_config=custom,
                base_url=None,
                client=ac,
                polling_policy=DEFAULT_POLLING_POLICY,
            )

    asyncio.run(main())
