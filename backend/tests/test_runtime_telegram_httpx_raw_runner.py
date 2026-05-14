"""Wiring tests for :mod:`app.runtime.telegram_httpx_raw_runner` (no network)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.runtime.telegram_httpx_raw_runner as httpx_raw_runner_mod
from app.runtime.polling_policy import (
    DEFAULT_POLLING_POLICY,
    OVERRIDE_HTTPX_TIMEOUT_MODE,
    NoopBackoffPolicy,
    NoopRetryPolicy,
    NoopTimeoutPolicy,
    PollingPolicy,
    PollingTimeoutDecision,
)
from app.runtime.runner import PollingRunSummary
from app.runtime.telegram_httpx_raw_runner import run_slice1_httpx_raw_iterations
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


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


def _empty_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "result": []}))


class _FakeAsyncPostClient:
    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None, dict[str, object]]] = []

    async def post(self, url: str, *, json: object | None = None, **kwargs: object) -> httpx.Response:
        self.calls.append((url, json, dict(kwargs)))
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"ok": True, "result": []}, request=req)


class _OverrideAllTimeoutPolicy:
    kind = "test_override"

    def __init__(self, httpx_timeout: httpx.Timeout) -> None:
        self._httpx_timeout = httpx_timeout

    def timeout_for_request(self, request_kind: str) -> PollingTimeoutDecision:
        return PollingTimeoutDecision(
            request_kind=request_kind,
            mode=OVERRIDE_HTTPX_TIMEOUT_MODE,
            httpx_timeout=self._httpx_timeout,
        )


def test_override_httpx_timeout_mode_reaches_getupdates_post_kwargs() -> None:
    expected_to = httpx.Timeout(12.34, connect=5.0)
    policy = PollingPolicy(
        timeout=_OverrideAllTimeoutPolicy(expected_to),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        fake = _FakeAsyncPostClient()
        s = await run_slice1_httpx_raw_iterations(
            "tok",
            1,
            base_url="https://ex.invalid/bot/",
            client=fake,  # type: ignore[arg-type]
            polling_policy=policy,
        )
        assert len(fake.calls) == 1
        url, body, kw = fake.calls[0]
        assert url.endswith("getUpdates")
        assert body == {"limit": 100, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
        assert kw.get("timeout") is expected_to
        assert s.fetch_failure_count == 0
        assert s.send_failure_count == 0

    _run(main())


def test_helper_returns_polling_run_summary() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            s = await run_slice1_httpx_raw_iterations(
                "tok",
                0,
                base_url="https://ex.invalid/bot/",
                client=ac,
            )
            assert isinstance(s, PollingRunSummary)

    _run(main())


def test_default_polling_policy_passed_to_build() -> None:
    orig = httpx_raw_runner_mod.build_slice1_httpx_raw_runtime_bundle
    captured: dict[str, object] = {}

    def spy(*a, **kw):
        captured.clear()
        captured.update(kw)
        return orig(*a, **kw)

    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(
                httpx_raw_runner_mod,
                "build_slice1_httpx_raw_runtime_bundle",
                side_effect=spy,
            ):
                await run_slice1_httpx_raw_iterations(
                    "tok",
                    0,
                    base_url="https://ex.invalid/bot/",
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
    orig = httpx_raw_runner_mod.build_slice1_httpx_raw_runtime_bundle

    def spy(*a, **kw):
        b = orig(*a, **kw)
        built.append(b)
        return b

    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(
                httpx_raw_runner_mod,
                "build_slice1_httpx_raw_runtime_bundle",
                side_effect=spy,
            ):
                await run_slice1_httpx_raw_iterations(
                    "tok",
                    0,
                    base_url="https://ex.invalid/bot/",
                    client=ac,
                    polling_policy=custom,
                )
        assert len(built) == 1
        assert built[0].client.polling_policy is custom

    _run(main())


def test_zero_iterations_empty_summary() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            s = await run_slice1_httpx_raw_iterations(
                "tok",
                0,
                base_url="https://ex.invalid/bot/",
                client=ac,
            )
            assert s == PollingRunSummary(
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

    _run(main())


def test_one_iteration_start_yields_send_count_one() -> None:
    raw = _update(message=_base_message(text="/start"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [raw]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            s = await run_slice1_httpx_raw_iterations(
                "tok",
                1,
                base_url="https://ex.invalid/bot/",
                client=ac,
                correlation_id=new_correlation_id(),
            )
            assert s.send_count == 1
            assert s.iterations_completed == 1

    _run(main())


def test_two_helper_runs_same_update_id_one_audit_each_fresh_bundle() -> None:
    raw = _update(update_id=7, message=_base_message(user_id=42, text="/start"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [raw]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    orig_build = httpx_raw_runner_mod.build_slice1_httpx_raw_runtime_bundle
    built: list = []

    def capturing_build(*a, **kw):
        b = orig_build(*a, **kw)
        built.append(b)
        return b

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch.object(
                httpx_raw_runner_mod,
                "build_slice1_httpx_raw_runtime_bundle",
                side_effect=capturing_build,
            ):
                await run_slice1_httpx_raw_iterations(
                    "tok",
                    1,
                    base_url="https://ex.invalid/bot/",
                    client=ac,
                    correlation_id=new_correlation_id(),
                )
                await run_slice1_httpx_raw_iterations(
                    "tok",
                    1,
                    base_url="https://ex.invalid/bot/",
                    client=ac,
                    correlation_id=new_correlation_id(),
                )
        assert len(built) == 2
        assert len(await built[0].bundle.composition.audit.recorded_events()) == 1
        assert len(await built[1].bundle.composition.audit.recorded_events()) == 1

    _run(main())


def test_aclose_runs_when_run_iterations_raises() -> None:
    mock_bundle = MagicMock()
    mock_bundle.bundle.runner.run_iterations = AsyncMock(side_effect=RuntimeError("boom"))
    mock_bundle.aclose = AsyncMock()

    async def main() -> None:
        with (
            patch.object(
                httpx_raw_runner_mod,
                "build_slice1_httpx_raw_runtime_bundle",
                return_value=mock_bundle,
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            await run_slice1_httpx_raw_iterations("tok", 1)
        mock_bundle.aclose.assert_awaited_once()

    _run(main())
