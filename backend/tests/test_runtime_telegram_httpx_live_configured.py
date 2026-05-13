"""Tests for :mod:`app.runtime.telegram_httpx_live_configured` (no network)."""

from __future__ import annotations

import asyncio
import inspect
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import app.runtime as rt
import app.runtime.telegram_httpx_live_configured as configured_mod
from app.application.bootstrap import Slice1Composition, build_slice1_composition
from app.runtime.polling import PollingRuntimeConfig
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
from app.runtime.telegram_httpx_live_app import Slice1HttpxLiveRuntimeApp
from app.runtime.telegram_httpx_live_configured import (
    build_slice1_httpx_live_runtime_app_from_config,
    build_slice1_httpx_live_runtime_app_from_config_async,
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


class _RecordingOverrideTimeoutPolicy:
    __slots__ = ("httpx_timeout", "decisions")

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


def test_factory_returns_app_and_accepts_runtime_config() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app_from_config(cfg, client=ac)
            assert isinstance(app, Slice1HttpxLiveRuntimeApp)
            assert isinstance(cfg, RuntimeConfig)
            await app.aclose()

    asyncio.run(main())


def test_default_polling_runtime_config() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app_from_config(cfg, client=ac)
            assert app.bundle.bundle.config == PollingRuntimeConfig()
            await app.aclose()

    asyncio.run(main())


def test_factory_default_polling_policy_identity() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app_from_config(cfg, client=ac)
            assert app.bundle.client.polling_policy is DEFAULT_POLLING_POLICY
            await app.aclose()

    asyncio.run(main())


def test_factory_custom_polling_policy_identity() -> None:
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
            app = build_slice1_httpx_live_runtime_app_from_config(
                cfg,
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
            app = build_slice1_httpx_live_runtime_app_from_config(
                cfg,
                polling_config=custom,
                client=ac,
            )
            assert app.bundle.bundle.config == custom
            await app.aclose()

    asyncio.run(main())


def test_configured_override_httpx_timeout_reaches_get_updates_post_identity() -> None:
    expected_timeout = httpx.Timeout(37.5, connect=3.0)
    timeout_policy = _RecordingOverrideTimeoutPolicy(expected_timeout)
    polling_policy = PollingPolicy(
        timeout=timeout_policy,
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )
    fake = _RecordingFakeAsyncClient()
    cfg = _minimal_runtime_config()

    async def main() -> None:
        app = build_slice1_httpx_live_runtime_app_from_config(
            cfg,
            client=cast(httpx.AsyncClient, fake),
            polling_policy=polling_policy,
        )
        summary = await app.run_iterations(1, correlation_id=new_correlation_id())
        assert len(timeout_policy.decisions) == 1
        td = timeout_policy.decisions[0]
        assert td.request_kind == LONG_POLL_FETCH_REQUEST
        assert td.mode == OVERRIDE_HTTPX_TIMEOUT_MODE
        assert td.httpx_timeout is expected_timeout
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

    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app_from_config(cfg, client=ac)
            summary = await app.run_iterations(1, correlation_id=new_correlation_id())
            assert summary.send_count == 1
            assert send_posts == 1
            await app.aclose()

    asyncio.run(main())


def test_runtime_package_exports() -> None:
    assert rt.build_slice1_httpx_live_runtime_app_from_config is build_slice1_httpx_live_runtime_app_from_config
    assert "build_slice1_httpx_live_runtime_app_from_config" in rt.__all__
    assert rt.build_slice1_httpx_live_runtime_app_from_config_async is build_slice1_httpx_live_runtime_app_from_config_async
    assert "build_slice1_httpx_live_runtime_app_from_config_async" in rt.__all__


@pytest.mark.parametrize(
    "raw",
    [
        "1",
        "true",
        "TRUE",
        " yes ",
    ],
)
def test_sync_config_builder_raises_when_postgres_repos_flag_on(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)
    cfg = _minimal_runtime_config()
    with pytest.raises(RuntimeError, match="build_slice1_httpx_live_runtime_app_from_config_async"):
        build_slice1_httpx_live_runtime_app_from_config(cfg)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0",
        "false",
        "no",
        "random",
    ],
)
def test_sync_config_builder_falsey_postgres_repos_env_does_not_trigger_sync_guard(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_live_runtime_app_from_config(cfg, client=ac)
            assert isinstance(app, Slice1HttpxLiveRuntimeApp)
            await app.aclose()

    asyncio.run(main())


def test_build_from_config_async_rejects_production_postgres_without_sslmode_before_postgres_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", "1")
    cfg = RuntimeConfig(
        bot_token="1234567890tok",
        database_url="postgresql://db.example.internal/app",
        app_env="production",
        debug_safe=False,
    )
    mock_apply = AsyncMock()
    mock_resolve = AsyncMock()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        with patch.object(
            configured_mod,
            "apply_slice1_postgres_migrations_from_runtime_config",
            new=mock_apply,
        ):
            with patch.object(
                configured_mod,
                "resolve_slice1_composition_for_runtime",
                new=mock_resolve,
            ):
                async with httpx.AsyncClient(transport=transport) as ac:
                    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
                        await build_slice1_httpx_live_runtime_app_from_config_async(cfg, client=ac)

    asyncio.run(main())
    mock_apply.assert_not_called()
    mock_resolve.assert_not_called()


def test_build_from_config_async_propagates_migration_failure_before_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", "1")
    cfg = RuntimeConfig(
        bot_token="1234567890tok",
        database_url="postgresql://db.example.internal/app?sslmode=require",
        app_env="production",
        debug_safe=False,
    )
    mock_apply = AsyncMock(side_effect=RuntimeError("migration failed"))
    mock_resolve = AsyncMock()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        with patch.object(
            configured_mod,
            "apply_slice1_postgres_migrations_from_runtime_config",
            new=mock_apply,
        ):
            with patch.object(
                configured_mod,
                "resolve_slice1_composition_for_runtime",
                new=mock_resolve,
            ):
                async with httpx.AsyncClient(transport=transport) as ac:
                    with pytest.raises(RuntimeError, match="migration failed"):
                        await build_slice1_httpx_live_runtime_app_from_config_async(cfg, client=ac)

    asyncio.run(main())
    mock_resolve.assert_not_called()


def test_build_from_config_async_uses_resolve_and_wires_composition_pg_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", "1")
    cfg = _minimal_runtime_config()
    sentinel_comp = build_slice1_composition()
    resolve_calls: list[RuntimeConfig] = []
    startup_order: list[str] = []

    class _FakePool:
        __slots__ = ("closed",)

        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    fake_pool = _FakePool()

    async def fake_apply_migrations(c: RuntimeConfig) -> None:
        startup_order.append("migrations")
        assert c is cfg

    async def fake_resolve(c: RuntimeConfig) -> tuple[Slice1Composition, _FakePool]:
        startup_order.append("resolve")
        resolve_calls.append(c)
        return sentinel_comp, fake_pool

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        with patch.object(
            configured_mod,
            "apply_slice1_postgres_migrations_from_runtime_config",
            new=fake_apply_migrations,
        ):
            with patch.object(
                configured_mod,
                "resolve_slice1_composition_for_runtime",
                new=fake_resolve,
            ):
                async with httpx.AsyncClient(transport=transport) as ac:
                    app = await build_slice1_httpx_live_runtime_app_from_config_async(cfg, client=ac)
        assert startup_order == ["migrations", "resolve"]
        assert resolve_calls == [cfg]
        assert app.bundle.bundle.composition is sentinel_comp
        assert app.bundle.pg_pool is fake_pool
        assert fake_pool.closed is False
        await app.aclose()
        assert fake_pool.closed is True

    asyncio.run(main())


def test_build_from_config_async_skips_migrations_helper_when_postgres_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLICE1_USE_POSTGRES_REPOS", raising=False)
    cfg = _minimal_runtime_config()
    mock_apply = AsyncMock()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        with patch.object(
            configured_mod,
            "apply_slice1_postgres_migrations_from_runtime_config",
            new=mock_apply,
        ):
            async with httpx.AsyncClient(transport=transport) as ac:
                app = await build_slice1_httpx_live_runtime_app_from_config_async(cfg, client=ac)
        mock_apply.assert_not_called()
        await app.aclose()

    asyncio.run(main())


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0",
        "false",
        "no",
        "random",
    ],
)
def test_build_from_config_async_skips_migrations_helper_for_falsey_postgres_repos_env(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)
    cfg = _minimal_runtime_config()
    mock_apply = AsyncMock()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        with patch.object(
            configured_mod,
            "apply_slice1_postgres_migrations_from_runtime_config",
            new=mock_apply,
        ):
            async with httpx.AsyncClient(transport=transport) as ac:
                app = await build_slice1_httpx_live_runtime_app_from_config_async(cfg, client=ac)
        mock_apply.assert_not_called()
        assert isinstance(app, Slice1HttpxLiveRuntimeApp)
        await app.aclose()

    asyncio.run(main())


def test_module_source_excludes_forbidden_tokens() -> None:
    src = inspect.getsource(configured_mod)
    lower = src.lower()
    for token in ("billing", "issuance", "admin", "webhook"):
        assert token not in lower


def test_module_source_no_env_cli_signal_sleep_backoff() -> None:
    src = inspect.getsource(configured_mod)
    lower = src.lower()
    for token in ("environ", "getenv", "dotenv", "argparse", "click", "signal", "sleep", "backoff"):
        assert token not in lower
