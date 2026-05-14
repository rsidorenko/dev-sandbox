"""Tests for :mod:`app.runtime.telegram_httpx_live_process` (no network, no real env reads)."""

from __future__ import annotations

from typing import Literal, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest

import app.runtime.telegram_httpx_live_env as env_mod
import app.runtime.telegram_httpx_live_process as process_mod
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
    build_slice1_httpx_live_runtime_app_from_env,
    build_slice1_httpx_live_runtime_app_from_env_async,
)
from app.runtime.telegram_httpx_live_process import (
    Slice1HttpxLiveProcess,
    build_slice1_httpx_live_process_from_config_async,
    build_slice1_httpx_live_process_from_env,
    build_slice1_httpx_live_process_from_env_async,
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


def test_factory_returns_process() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                proc = build_slice1_httpx_live_process_from_env(client=ac)
            assert isinstance(proc, Slice1HttpxLiveProcess)
            assert isinstance(proc.app, Slice1HttpxLiveRuntimeApp)
            assert proc.app.bundle.client.polling_policy is DEFAULT_POLLING_POLICY
            assert proc.control.stop_requested is False
            await proc.aclose()

    _run(main())


def test_factory_custom_polling_policy_reaches_client() -> None:
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
                proc = build_slice1_httpx_live_process_from_env(
                    client=ac,
                    polling_policy=custom,
                )
            assert proc.app.bundle.client.polling_policy is custom
            await proc.aclose()

    _run(main())


def test_factory_uses_build_from_env() -> None:
    cfg = _minimal_runtime_config()
    calls: list[dict] = []

    def spy(**kwargs):
        calls.append(kwargs)
        return build_slice1_httpx_live_runtime_app_from_env(**kwargs)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with (
                patch.object(env_mod, "load_runtime_config", return_value=cfg),
                patch.object(
                    process_mod,
                    "build_slice1_httpx_live_runtime_app_from_env",
                    side_effect=spy,
                ),
            ):
                build_slice1_httpx_live_process_from_env(client=ac)
            assert len(calls) == 1
            assert calls[0]["client"] is ac
            assert calls[0]["polling_policy"] is DEFAULT_POLLING_POLICY

    _run(main())


def test_factory_uses_build_from_config_async() -> None:
    cfg = _minimal_runtime_config()
    calls: list[tuple[RuntimeConfig, dict]] = []

    mock_app = MagicMock(spec=Slice1HttpxLiveRuntimeApp)

    async def spy(config: RuntimeConfig, **kwargs):
        calls.append((config, kwargs))
        return mock_app

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(
                process_mod,
                "build_slice1_httpx_live_runtime_app_from_config_async",
                side_effect=spy,
            ):
                proc = await build_slice1_httpx_live_process_from_config_async(
                    cfg,
                    client=ac,
                )
            assert isinstance(proc, Slice1HttpxLiveProcess)
            assert proc.app is mock_app
            assert proc.control.stop_requested is False
            assert len(calls) == 1
            assert calls[0][0] is cfg
            assert calls[0][1]["client"] is ac
            assert calls[0][1]["polling_policy"] is DEFAULT_POLLING_POLICY

    _run(main())


@pytest.mark.parametrize(
    "raw",
    ["", "0", "false", "no", "random"],
)
def test_factory_uses_build_from_config_async_with_falsey_postgres_repos_flag(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)
    cfg = _minimal_runtime_config()
    calls: list[tuple[RuntimeConfig, dict]] = []

    mock_app = MagicMock(spec=Slice1HttpxLiveRuntimeApp)

    async def spy(config: RuntimeConfig, **kwargs):
        calls.append((config, kwargs))
        return mock_app

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(
                process_mod,
                "build_slice1_httpx_live_runtime_app_from_config_async",
                side_effect=spy,
            ):
                proc = await build_slice1_httpx_live_process_from_config_async(
                    cfg,
                    client=ac,
                )
            assert isinstance(proc, Slice1HttpxLiveProcess)
            assert proc.app is mock_app
            assert proc.control.stop_requested is False
            assert len(calls) == 1
            assert calls[0][0] is cfg
            assert calls[0][1]["client"] is ac
            assert calls[0][1]["polling_policy"] is DEFAULT_POLLING_POLICY

    _run(main())


def test_factory_uses_build_from_env_async() -> None:
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
            with (
                patch.object(env_mod, "load_runtime_config", return_value=cfg),
                patch.object(
                    process_mod,
                    "build_slice1_httpx_live_runtime_app_from_env_async",
                    side_effect=spy,
                ),
            ):
                await build_slice1_httpx_live_process_from_env_async(client=ac)
            assert len(calls) == 1
            assert calls[0]["client"] is ac
            assert calls[0]["polling_policy"] is DEFAULT_POLLING_POLICY

    _run(main())


@pytest.mark.parametrize(
    "raw",
    ["", "0", "false", "no", "random"],
)
def test_env_process_from_env_async_delegates_when_postgres_repos_flag_is_falsey(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    """Falsey SLICE1_USE_POSTGRES_REPOS values must not break async env process delegation."""
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)
    calls: list[dict] = []
    mock_app = MagicMock(spec=Slice1HttpxLiveRuntimeApp)

    async def spy(**kwargs: object) -> object:
        calls.append(kwargs)
        return mock_app

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(
                process_mod,
                "build_slice1_httpx_live_runtime_app_from_env_async",
                side_effect=spy,
            ):
                proc = await build_slice1_httpx_live_process_from_env_async(client=ac)
            assert isinstance(proc, Slice1HttpxLiveProcess)
            assert proc.app is mock_app
            assert proc.control.stop_requested is False
            assert len(calls) == 1
            assert calls[0]["client"] is ac
            assert calls[0]["polling_policy"] is DEFAULT_POLLING_POLICY

    _run(main())


def test_build_process_from_env_async_fail_fast_when_runtime_app_builder_raises() -> None:
    cfg = _minimal_runtime_config()
    builder_calls = 0

    async def failing_builder(**kwargs: object) -> Slice1HttpxLiveRuntimeApp:
        nonlocal builder_calls
        builder_calls += 1
        raise RuntimeError("migration failed")

    mock_process_cls = MagicMock()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with (
                patch.object(env_mod, "load_runtime_config", return_value=cfg),
                patch.object(
                    process_mod,
                    "build_slice1_httpx_live_runtime_app_from_env_async",
                    side_effect=failing_builder,
                ),
                patch.object(process_mod, "Slice1HttpxLiveProcess", mock_process_cls),
            ):
                with pytest.raises(RuntimeError, match="migration failed"):
                    await build_slice1_httpx_live_process_from_env_async(client=ac)
            assert builder_calls == 1

    _run(main())
    assert mock_process_cls.call_count == 0


def test_build_process_from_env_fail_fast_when_runtime_app_builder_raises() -> None:
    cfg = _minimal_runtime_config()
    builder_calls = 0

    def failing_builder(**kwargs: object) -> Slice1HttpxLiveRuntimeApp:
        nonlocal builder_calls
        builder_calls += 1
        raise RuntimeError("sync env app build failed")

    mock_process_cls = MagicMock()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with (
                patch.object(env_mod, "load_runtime_config", return_value=cfg),
                patch.object(
                    process_mod,
                    "build_slice1_httpx_live_runtime_app_from_env",
                    side_effect=failing_builder,
                ),
                patch.object(process_mod, "Slice1HttpxLiveProcess", mock_process_cls),
            ):
                with pytest.raises(RuntimeError, match="sync env app build failed"):
                    build_slice1_httpx_live_process_from_env(client=ac)
            assert builder_calls == 1

    _run(main())
    assert mock_process_cls.call_count == 0


def test_build_process_from_config_async_fail_fast_when_runtime_app_builder_raises() -> None:
    cfg = _minimal_runtime_config()
    builder_calls = 0

    async def failing_builder(config: RuntimeConfig, **kwargs: object) -> Slice1HttpxLiveRuntimeApp:
        nonlocal builder_calls
        builder_calls += 1
        raise RuntimeError("config app build failed")

    mock_process_cls = MagicMock()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with (
                patch.object(
                    process_mod,
                    "build_slice1_httpx_live_runtime_app_from_config_async",
                    side_effect=failing_builder,
                ),
                patch.object(process_mod, "Slice1HttpxLiveProcess", mock_process_cls),
            ):
                with pytest.raises(RuntimeError, match="config app build failed"):
                    await build_slice1_httpx_live_process_from_config_async(
                        cfg,
                        client=ac,
                    )
            assert builder_calls == 1

    _run(main())
    assert mock_process_cls.call_count == 0


def test_request_stop_idempotent() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                proc = build_slice1_httpx_live_process_from_env(client=ac)
            proc.request_stop()
            assert proc.control.stop_requested is True
            proc.request_stop()
            assert proc.control.stop_requested is True
            await proc.aclose()

    _run(main())


def test_request_stop_before_run_empty_summary() -> None:
    cfg = _minimal_runtime_config()

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                proc = build_slice1_httpx_live_process_from_env(client=ac)
            proc.request_stop()
            summary = await proc.run_until_stopped()
            assert summary == _empty_summary()
            await proc.aclose()

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
                proc = build_slice1_httpx_live_process_from_env(client=ac)
            summary = await proc.run_until_stopped(
                correlation_id=new_correlation_id(),
                max_iterations=1,
            )
            assert summary.send_count == 1
            await proc.aclose()

    _run(main())


def test_override_httpx_timeout_mode_passes_through_public_process_path_to_get_updates_post() -> None:
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
            proc = build_slice1_httpx_live_process_from_env(
                client=cast("httpx.AsyncClient", fake),
                polling_policy=polling_policy,
            )
        summary = await proc.run_until_stopped(max_iterations=1)
        assert len(timeout_policy.decisions) == 1
        d0 = timeout_policy.decisions[0]
        assert d0.request_kind == LONG_POLL_FETCH_REQUEST
        assert d0.mode == OVERRIDE_HTTPX_TIMEOUT_MODE
        assert d0.httpx_timeout is expected_timeout
        assert len(fake.post_calls) == 1
        url, body, kw = fake.post_calls[0]
        assert url.endswith("getUpdates")
        assert body == {"limit": 100, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
        assert "timeout" in kw
        assert kw["timeout"] is expected_timeout
        assert summary.fetch_failure_count == 0
        assert summary.send_failure_count == 0
        await proc.aclose()

    _run(main())


def test_aclose_delegates_to_app(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _minimal_runtime_config()
    aclose_calls = 0
    real_aclose = Slice1HttpxLiveRuntimeApp.aclose

    async def counting_aclose(self: Slice1HttpxLiveRuntimeApp) -> None:
        nonlocal aclose_calls
        aclose_calls += 1
        await real_aclose(self)

    monkeypatch.setattr(Slice1HttpxLiveRuntimeApp, "aclose", counting_aclose)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(env_mod, "load_runtime_config", return_value=cfg):
                proc = build_slice1_httpx_live_process_from_env(client=ac)
            await proc.aclose()

    _run(main())
    assert aclose_calls == 1
