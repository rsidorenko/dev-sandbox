"""Fail-fast when sync env/process builders cannot honor SLICE1_USE_POSTGRES_REPOS."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest

import app.runtime.telegram_httpx_live_configured as configured_mod
import app.runtime.telegram_httpx_live_env as env_mod
from app.application.bootstrap import build_slice1_composition
from app.runtime.telegram_httpx_live_env import build_slice1_httpx_live_runtime_app_from_env
from app.runtime.telegram_httpx_live_process import (
    Slice1HttpxLiveProcess,
    build_slice1_httpx_live_process_from_env,
    build_slice1_httpx_live_process_from_env_async,
)
from app.security.config import RuntimeConfig


def _cfg() -> RuntimeConfig:
    return RuntimeConfig(
        bot_token="1234567890tok",
        database_url="postgresql://localhost/db",
        app_env="development",
        debug_safe=False,
    )


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes "])
def test_sync_env_builder_raises_when_postgres_repos_flag_on(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=_cfg()):
                with pytest.raises(RuntimeError, match="build_slice1_httpx_live_runtime_app_from_env_async"):
                    build_slice1_httpx_live_runtime_app_from_env(client=ac)

    asyncio.run(main())


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes "])
def test_sync_process_builder_raises_when_postgres_repos_flag_on(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=_cfg()):
                with pytest.raises(RuntimeError, match="build_slice1_httpx_live_runtime_app_from_env_async"):
                    build_slice1_httpx_live_process_from_env(client=ac)

    asyncio.run(main())


@pytest.mark.parametrize("raw", ["", "0", "false", "no", "random"])
def test_sync_env_builder_does_not_raise_when_postgres_repos_falsey(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=_cfg()):
                app = build_slice1_httpx_live_runtime_app_from_env(client=ac)
            await app.aclose()

    asyncio.run(main())


@pytest.mark.parametrize("raw", ["", "0", "false", "no", "random"])
def test_sync_process_builder_does_not_raise_when_postgres_repos_falsey(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=_cfg()):
                proc = build_slice1_httpx_live_process_from_env(client=ac)
            await proc.aclose()

    asyncio.run(main())


async def _fake_resolve_slice1_composition(*_args, **_kwargs):
    return build_slice1_composition(), None


async def _noop_apply_slice1_postgres_migrations(*_args, **_kwargs) -> None:
    return None


def test_async_process_builder_does_not_use_sync_guard_when_postgres_repos_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", "1")

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with (
                patch.object(env_mod, "load_runtime_config", return_value=_cfg()),
                patch.object(
                    configured_mod,
                    "apply_slice1_postgres_migrations_from_runtime_config",
                    new=_noop_apply_slice1_postgres_migrations,
                ),
                patch.object(
                    configured_mod,
                    "resolve_slice1_composition_for_runtime",
                    new=_fake_resolve_slice1_composition,
                ),
            ):
                proc = await build_slice1_httpx_live_process_from_env_async(client=ac)
            assert isinstance(proc, Slice1HttpxLiveProcess)
            await proc.aclose()

    asyncio.run(main())
