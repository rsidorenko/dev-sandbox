"""Tests for :mod:`app.runtime.telegram_httpx_raw_process` (no network, no real env reads)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

import app.runtime.telegram_httpx_raw_env as env_mod
import app.runtime.telegram_httpx_raw_process as process_mod
from app.runtime.polling_policy import (
    DEFAULT_POLLING_POLICY,
    NoopBackoffPolicy,
    NoopRetryPolicy,
    NoopTimeoutPolicy,
    PollingPolicy,
)
from app.runtime.runner import PollingRunSummary
from app.runtime.telegram_httpx_raw_app import Slice1HttpxRawRuntimeApp
from app.runtime.telegram_httpx_raw_env import build_slice1_httpx_raw_runtime_app_from_env
from app.runtime.telegram_httpx_raw_process import (
    Slice1HttpxRawProcess,
    build_slice1_httpx_raw_process_from_env,
)
from app.security.config import RuntimeConfig
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


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


def test_factory_returns_process() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                proc = build_slice1_httpx_raw_process_from_env(client=ac)
            assert isinstance(proc, Slice1HttpxRawProcess)
            assert isinstance(proc.app, Slice1HttpxRawRuntimeApp)
            await proc.aclose()

    _run(main())


def test_factory_uses_build_from_env() -> None:
    cfg = _minimal_runtime_config()
    calls: list[dict] = []

    def spy(**kwargs):
        calls.append(kwargs)
        return build_slice1_httpx_raw_runtime_app_from_env(**kwargs)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with (
                patch.object(env_mod, "load_runtime_config", return_value=cfg),
                patch.object(
                    process_mod,
                    "build_slice1_httpx_raw_runtime_app_from_env",
                    side_effect=spy,
                ),
            ):
                build_slice1_httpx_raw_process_from_env(client=ac)
            assert len(calls) == 1
            assert calls[0]["client"] is ac
            assert calls[0]["polling_policy"] is DEFAULT_POLLING_POLICY

    _run(main())


def test_factory_default_polling_policy_on_client() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                proc = build_slice1_httpx_raw_process_from_env(client=ac)
            assert proc.app.bundle.client.polling_policy is DEFAULT_POLLING_POLICY
            await proc.aclose()

    _run(main())


def test_factory_custom_polling_policy_identity_on_client() -> None:
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
                proc = build_slice1_httpx_raw_process_from_env(client=ac, polling_policy=custom)
            assert proc.app.bundle.client.polling_policy is custom
            await proc.aclose()

    _run(main())


def test_run_iterations_zero_empty_summary() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                proc = build_slice1_httpx_raw_process_from_env(client=ac)
            s = await proc.run_iterations(0)
            assert s == _empty_summary()
            await proc.aclose()

    _run(main())


def test_run_iterations_one_start_send_count_one() -> None:
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
                proc = build_slice1_httpx_raw_process_from_env(client=ac)
            summary = await proc.run_iterations(1, correlation_id=new_correlation_id())
            assert summary.send_count == 1
            await proc.aclose()

    _run(main())


def test_poll_once_empty_fetch_ok() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                proc = build_slice1_httpx_raw_process_from_env(client=ac)
            r = await proc.poll_once(correlation_id=new_correlation_id())
            assert r.fetch_failure_count == 0
            assert r.raw_received_count == 0
            await proc.aclose()

    _run(main())


def test_two_poll_once_same_update_replay_second_noop_one_audit() -> None:
    raw = _update(update_id=5, message=_base_message(user_id=42, text="/start"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [raw]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                proc = build_slice1_httpx_raw_process_from_env(client=ac)
            cid = new_correlation_id()
            r1 = await proc.poll_once(correlation_id=cid)
            r2 = await proc.poll_once(correlation_id=cid)
            assert r1.send_count == 1 and r1.noop_count == 0
            assert r2.send_count == 0 and r2.noop_count == 1
            assert len(await proc.app.bundle.bundle.composition.audit.recorded_events()) == 1
            await proc.aclose()

    _run(main())


def test_aclose_delegates_to_app(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _minimal_runtime_config()
    aclose_calls = 0
    real_aclose = Slice1HttpxRawRuntimeApp.aclose

    async def counting_aclose(self: Slice1HttpxRawRuntimeApp) -> None:
        nonlocal aclose_calls
        aclose_calls += 1
        await real_aclose(self)

    monkeypatch.setattr(Slice1HttpxRawRuntimeApp, "aclose", counting_aclose)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                proc = build_slice1_httpx_raw_process_from_env(client=ac)
            await proc.aclose()

    _run(main())
    assert aclose_calls == 1
