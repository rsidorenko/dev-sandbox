"""Tests for :mod:`app.runtime.telegram_httpx_live_loop` (no network)."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Literal
from unittest.mock import patch

import httpx
import pytest

import app.runtime as rt
import app.runtime.telegram_httpx_live_loop as httpx_live_loop_mod
from app.runtime import run_slice1_httpx_live_until_stopped
from app.runtime.live_loop import LoopControl, Slice1LiveRawPollingLoop
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
from app.runtime.telegram_httpx_live_startup import (
    Slice1HttpxLiveRuntimeBundle,
    build_slice1_httpx_live_runtime_bundle,
)
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


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


def test_helper_returns_polling_run_summary() -> None:
    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            summary = await run_slice1_httpx_live_until_stopped(
                "t",
                LoopControl(),
                client=ac,
                max_iterations=1,
            )
            assert isinstance(summary, PollingRunSummary)

    _run(main())


def test_default_polling_policy_passed_to_build() -> None:
    orig = httpx_live_loop_mod.build_slice1_httpx_live_runtime_bundle
    captured: dict[str, object] = {}

    def spy(*a, **kw):
        captured.clear()
        captured.update(kw)
        return orig(*a, **kw)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(
                httpx_live_loop_mod,
                "build_slice1_httpx_live_runtime_bundle",
                side_effect=spy,
            ):
                await run_slice1_httpx_live_until_stopped(
                    "t",
                    LoopControl(stop_requested=True),
                    client=ac,
                )
        assert captured["polling_policy"] is DEFAULT_POLLING_POLICY

    _run(main())


def test_custom_polling_policy_reaches_bundle_client() -> None:
    custom = PollingPolicy(
        timeout=NoopTimeoutPolicy(),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )
    built: list = []
    orig = httpx_live_loop_mod.build_slice1_httpx_live_runtime_bundle

    def spy(*a, **kw):
        b = orig(*a, **kw)
        built.append(b)
        return b

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(
                httpx_live_loop_mod,
                "build_slice1_httpx_live_runtime_bundle",
                side_effect=spy,
            ):
                await run_slice1_httpx_live_until_stopped(
                    "t",
                    LoopControl(stop_requested=True),
                    client=ac,
                    polling_policy=custom,
                )
        assert len(built) == 1
        assert built[0].client.polling_policy is custom

    _run(main())


def test_override_httpx_timeout_mode_passes_through_helper_to_getupdates_post() -> None:
    expected_timeout = httpx.Timeout(42.0, connect=5.0)
    polling_policy = PollingPolicy(
        timeout=_FixedOverrideTimeoutPolicy(expected_timeout),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        fake = _RecordingFakeAsyncClient()
        summary = await run_slice1_httpx_live_until_stopped(
            "t",
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


def test_override_httpx_timeout_mode_direct_public_live_loop_path_reaches_getupdates_post() -> None:
    expected_timeout = httpx.Timeout(42.0, connect=5.0)
    timeout_policy = _RecordingOverrideTimeoutPolicy(expected_timeout)
    polling_policy = PollingPolicy(
        timeout=timeout_policy,
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        fake = _RecordingFakeAsyncClient()
        summary = await run_slice1_httpx_live_until_stopped(
            "t",
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


def test_override_httpx_timeout_mode_public_live_loop_module_entrypoint_reaches_getupdates_post() -> None:
    expected_timeout = httpx.Timeout(42.0, connect=5.0)
    timeout_policy = _RecordingOverrideTimeoutPolicy(expected_timeout)
    polling_policy = PollingPolicy(
        timeout=timeout_policy,
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        fake = _RecordingFakeAsyncClient()
        summary = await httpx_live_loop_mod.run_slice1_httpx_live_until_stopped(
            "t",
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
        url, body, kwargs = fake.post_calls[0]
        assert url.endswith("getUpdates")
        assert body == {"limit": 100, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
        assert kwargs["timeout"] is expected_timeout
        assert summary.fetch_failure_count == 0
        assert summary.send_failure_count == 0

    _run(main())


def test_stop_requested_before_start_empty_summary() -> None:
    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            summary = await run_slice1_httpx_live_until_stopped(
                "t",
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

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            summary = await run_slice1_httpx_live_until_stopped(
                "t",
                LoopControl(),
                client=ac,
                correlation_id=new_correlation_id(),
                max_iterations=1,
            )
            assert summary.send_count == 1

    _run(main())


def test_uses_external_loop_control_instance(monkeypatch: pytest.MonkeyPatch) -> None:
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
            external = LoopControl()
            ref = build_slice1_httpx_live_runtime_bundle("t", client=ac)
            try:
                await run_slice1_httpx_live_until_stopped("t", external, client=ac)
            finally:
                await ref.aclose()
            assert len(captured) == 1
            assert captured[0] is external
            assert captured[0] is not ref.bundle.control

    _run(main())


def test_aclose_runs_when_run_until_stopped_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    aclose_calls = 0
    real_aclose = Slice1HttpxLiveRuntimeBundle.aclose

    async def counting_aclose(self: Slice1HttpxLiveRuntimeBundle) -> None:
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

    monkeypatch.setattr(Slice1HttpxLiveRuntimeBundle, "aclose", counting_aclose)
    monkeypatch.setattr(Slice1LiveRawPollingLoop, "run_until_stopped", boom)

    async def main() -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
        )
        async with httpx.AsyncClient(transport=transport) as ac:
            with pytest.raises(RuntimeError, match="forced loop failure"):
                await run_slice1_httpx_live_until_stopped("t", LoopControl(), client=ac)
        assert aclose_calls == 1

    _run(main())


def test_app_runtime_exports_helper() -> None:
    assert rt.run_slice1_httpx_live_until_stopped is run_slice1_httpx_live_until_stopped
    assert "run_slice1_httpx_live_until_stopped" in rt.__all__


def test_module_source_excludes_forbidden_tokens() -> None:
    src = inspect.getsource(httpx_live_loop_mod)
    lower = src.lower()
    for token in ("billing", "issuance", "admin", "webhook"):
        assert token not in lower


def test_module_source_no_env_cli_signal_sleep_backoff() -> None:
    src = inspect.getsource(httpx_live_loop_mod)
    lower = src.lower()
    for token in ("environ", "getenv", "dotenv", "argparse", "click", "signal", "sleep", "backoff"):
        assert token not in lower
