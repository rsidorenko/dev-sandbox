"""Tests for :mod:`app.runtime.telegram_httpx_live_env` (no network, no real env reads)."""

from __future__ import annotations

import asyncio
import inspect
from typing import Literal, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.runtime as rt
import app.runtime.telegram_httpx_live_env as env_mod
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
from app.runtime.telegram_httpx_live_app import Slice1HttpxLiveRuntimeApp
from app.runtime.telegram_httpx_live_env import (
    build_slice1_httpx_live_runtime_app_from_env,
    build_slice1_httpx_live_runtime_app_from_env_async,
)
from app.security.config import ConfigurationError, RuntimeConfig
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


def test_factory_returns_app_and_uses_load_runtime_config() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg) as load_mock:
                app = build_slice1_httpx_live_runtime_app_from_env(client=ac)
            load_mock.assert_called_once_with()
            assert isinstance(app, Slice1HttpxLiveRuntimeApp)
            assert isinstance(cfg, RuntimeConfig)
            await app.aclose()

    asyncio.run(main())


def test_override_httpx_timeout_mode_direct_env_path_reaches_get_updates_post() -> None:
    cfg = _minimal_runtime_config()
    expected_timeout = httpx.Timeout(37.5, connect=3.0)
    timeout_policy = _RecordingOverrideTimeoutPolicy(expected_timeout)
    polling_policy = PollingPolicy(
        timeout=timeout_policy,
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )
    fake = _RecordingFakeAsyncClient()

    async def main() -> None:
        with patch.object(env_mod, "load_runtime_config", return_value=cfg):
            app = build_slice1_httpx_live_runtime_app_from_env(
                client=cast("httpx.AsyncClient", fake),
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


def test_default_polling_runtime_config() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                app = build_slice1_httpx_live_runtime_app_from_env(client=ac)
            assert app.bundle.bundle.config == PollingRuntimeConfig()
            await app.aclose()

    asyncio.run(main())


def test_default_polling_policy_identity() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                app = build_slice1_httpx_live_runtime_app_from_env(client=ac)
            assert app.bundle.client.polling_policy is DEFAULT_POLLING_POLICY
            await app.aclose()

    asyncio.run(main())


def test_custom_polling_policy_identity() -> None:
    cfg = _minimal_runtime_config()
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
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                app = build_slice1_httpx_live_runtime_app_from_env(
                    client=ac,
                    polling_policy=custom,
                )
            assert app.bundle.client.polling_policy is custom
            await app.aclose()

    asyncio.run(main())


def test_optional_polling_config_passed_through() -> None:
    cfg = _minimal_runtime_config()
    custom = PollingRuntimeConfig(max_updates_per_batch=7)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                app = build_slice1_httpx_live_runtime_app_from_env(
                    polling_config=custom,
                    client=ac,
                )
            assert app.bundle.bundle.config == custom
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

    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                app = build_slice1_httpx_live_runtime_app_from_env(client=ac)
            summary = await app.run_iterations(1, correlation_id=new_correlation_id())
            assert summary.send_count == 1
            assert send_posts == 1
            await app.aclose()

    asyncio.run(main())


def test_runtime_package_exports() -> None:
    assert rt.build_slice1_httpx_live_runtime_app_from_env is build_slice1_httpx_live_runtime_app_from_env
    assert "build_slice1_httpx_live_runtime_app_from_env" in rt.__all__


def test_env_async_builder_fail_fast_tls_policy_before_config_async_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-local DSN without sslmode is rejected in production before composition/async wiring."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example.internal/app")
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", "1")
    mock_from_config_async = AsyncMock()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(
                env_mod,
                "build_slice1_httpx_live_runtime_app_from_config_async",
                new=mock_from_config_async,
            ):
                with pytest.raises(ConfigurationError, match="DATABASE_URL"):
                    await build_slice1_httpx_live_runtime_app_from_env_async(client=ac)
        assert mock_from_config_async.await_count == 0

    asyncio.run(main())


def test_env_async_builder_delegates_to_config_async_builder() -> None:
    cfg = _minimal_runtime_config()
    sentinel = MagicMock(spec=Slice1HttpxLiveRuntimeApp)
    mock_from_config_async = AsyncMock(return_value=sentinel)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with patch.object(
                    env_mod,
                    "build_slice1_httpx_live_runtime_app_from_config_async",
                    new=mock_from_config_async,
                ):
                    app = await build_slice1_httpx_live_runtime_app_from_env_async(client=ac)
        mock_from_config_async.assert_awaited_once()
        (pos_cfg,), kwargs = mock_from_config_async.await_args
        assert pos_cfg is cfg
        assert kwargs["client"] is ac
        assert app is sentinel

    asyncio.run(main())


@pytest.mark.parametrize("raw", ["", "0", "false", "no", "random"])
def test_env_async_builder_falsey_slice1_postgres_repos_still_delegates_to_config_async_builder(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)
    cfg = _minimal_runtime_config()
    sentinel = MagicMock(spec=Slice1HttpxLiveRuntimeApp)
    mock_from_config_async = AsyncMock(return_value=sentinel)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                with patch.object(
                    env_mod,
                    "build_slice1_httpx_live_runtime_app_from_config_async",
                    new=mock_from_config_async,
                ):
                    app = await build_slice1_httpx_live_runtime_app_from_env_async(client=ac)
        mock_from_config_async.assert_awaited_once()
        (pos_cfg,), kwargs = mock_from_config_async.await_args
        assert pos_cfg is cfg
        assert kwargs["client"] is ac
        assert app is sentinel

    asyncio.run(main())


def test_module_source_excludes_forbidden_tokens() -> None:
    src = inspect.getsource(env_mod)
    lower = src.lower()
    for token in ("billing", "issuance", "admin", "webhook"):
        assert token not in lower


def test_module_source_no_manual_env_cli_signal_sleep_backoff() -> None:
    src = inspect.getsource(env_mod)
    lower = src.lower()
    for token in ("environ", "getenv", "dotenv", "argparse", "click", "signal", "sleep", "backoff"):
        assert token not in lower
