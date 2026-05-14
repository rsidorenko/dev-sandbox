"""Wiring tests for :mod:`app.runtime.telegram_httpx_raw_app` (no network)."""

from __future__ import annotations

import inspect

import httpx

import app.runtime as rt
import app.runtime.telegram_httpx_raw_app as httpx_raw_app_mod
from app.runtime.polling_policy import (
    DEFAULT_POLLING_POLICY,
    NoopBackoffPolicy,
    NoopRetryPolicy,
    NoopTimeoutPolicy,
    PollingPolicy,
)
from app.runtime.runner import PollingRunSummary
from app.runtime.telegram_httpx_raw_app import (
    Slice1HttpxRawRuntimeApp,
    build_slice1_httpx_raw_runtime_app,
)
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


def test_factory_returns_app() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_raw_runtime_app("tok", base_url="https://ex.invalid/bot/", client=ac)
            assert isinstance(app, Slice1HttpxRawRuntimeApp)

    _run(main())


def test_default_polling_policy_is_default() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_raw_runtime_app("tok", base_url="https://ex.invalid/bot/", client=ac)
            assert app.bundle.client.polling_policy is DEFAULT_POLLING_POLICY

    _run(main())


def test_custom_polling_policy_identity() -> None:
    custom = PollingPolicy(
        timeout=NoopTimeoutPolicy(),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_raw_runtime_app(
                "tok",
                base_url="https://ex.invalid/bot/",
                client=ac,
                polling_policy=custom,
            )
            assert app.bundle.client.polling_policy is custom

    _run(main())


def test_run_iterations_zero_empty_summary() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_raw_runtime_app("tok", base_url="https://ex.invalid/bot/", client=ac)
            s = await app.run_iterations(0)
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


def test_run_iterations_one_start_send_count_one() -> None:
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
            app = build_slice1_httpx_raw_runtime_app("tok", base_url="https://ex.invalid/bot/", client=ac)
            s = await app.run_iterations(1, correlation_id=new_correlation_id())
            assert s.send_count == 1

    _run(main())


def test_poll_once_returns_raw_batch_result() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_raw_runtime_app("tok", base_url="https://ex.invalid/bot/", client=ac)
            r = await app.poll_once(correlation_id=new_correlation_id())
            assert r.fetch_failure_count == 0
            assert r.raw_received_count == 0

    _run(main())


def test_same_app_twice_same_update_replay_second_noop_one_audit() -> None:
    raw = _update(update_id=5, message=_base_message(user_id=42, text="/start"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [raw]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_raw_runtime_app("tok", base_url="https://ex.invalid/bot/", client=ac)
            cid = new_correlation_id()
            r1 = await app.poll_once(correlation_id=cid)
            r2 = await app.poll_once(correlation_id=cid)
            assert r1.send_count == 1 and r1.noop_count == 0
            assert r2.send_count == 0 and r2.noop_count == 1
            assert len(await app.bundle.bundle.composition.audit.recorded_events()) == 1

    _run(main())


def test_aclose_idempotent() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            app = build_slice1_httpx_raw_runtime_app("tok", base_url="https://ex.invalid/bot/", client=ac)
            await app.aclose()
            await app.aclose()

    _run(main())


def test_app_runtime_reexports_raw_app() -> None:
    assert rt.Slice1HttpxRawRuntimeApp is Slice1HttpxRawRuntimeApp
    assert rt.build_slice1_httpx_raw_runtime_app is build_slice1_httpx_raw_runtime_app
    assert "Slice1HttpxRawRuntimeApp" in rt.__all__
    assert "build_slice1_httpx_raw_runtime_app" in rt.__all__


def test_httpx_raw_app_module_avoids_forbidden_tokens() -> None:
    src = inspect.getsource(httpx_raw_app_mod)
    lower = src.lower()
    for w in ("billing", "issuance", "admin", "webhook"):
        assert w not in lower
    for s in ("environ", "getenv", "dotenv", "argparse", "click", "signal", "sleep", "backoff"):
        assert s not in src
