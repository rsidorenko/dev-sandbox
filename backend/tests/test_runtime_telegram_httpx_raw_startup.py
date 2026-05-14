"""Wiring tests for :mod:`app.runtime.telegram_httpx_raw_startup` (no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.runtime import accept_mapping_runtime_update
from app.runtime.polling import PollingRuntimeConfig
from app.runtime.polling_policy import (
    DEFAULT_POLLING_POLICY,
    NoopBackoffPolicy,
    NoopRetryPolicy,
    NoopTimeoutPolicy,
    PollingPolicy,
)
from app.runtime.raw_startup import Slice1InMemoryRawRuntimeBundle
from app.runtime.telegram_httpx_raw_startup import (
    Slice1HttpxRawRuntimeBundle,
    build_slice1_httpx_raw_runtime_bundle,
)
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


def _json_body(request: httpx.Request) -> dict:
    if not request.content:
        return {}
    return json.loads(request.content.decode())


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


def test_builder_returns_httpx_raw_bundle_and_inner_type() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_raw_runtime_bundle("tok", base_url="https://ex.invalid/bot/", client=ac)
            assert isinstance(b, Slice1HttpxRawRuntimeBundle)
            assert isinstance(b.bundle, Slice1InMemoryRawRuntimeBundle)

    _run(main())


def test_builder_default_polling_policy_is_module_default() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_raw_runtime_bundle("tok", base_url="https://ex.invalid/bot/", client=ac)
            assert b.client.polling_policy is DEFAULT_POLLING_POLICY

    _run(main())


def test_builder_custom_polling_policy_identity() -> None:
    custom = PollingPolicy(
        timeout=NoopTimeoutPolicy(),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )

    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_raw_runtime_bundle(
                "tok",
                base_url="https://ex.invalid/bot/",
                client=ac,
                polling_policy=custom,
            )
            assert b.client.polling_policy is custom

    _run(main())


def test_config_none_yields_default_polling_config() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_raw_runtime_bundle("tok", base_url="https://ex.invalid/bot/", client=ac)
            assert b.bundle.config == PollingRuntimeConfig()

    _run(main())


def test_custom_config_reaches_inner_bundle_and_limits_fetch() -> None:
    u1 = _update(update_id=1, message=_base_message(text="/start"))
    u2 = _update(update_id=2, message=_base_message(text="/start"))
    u3 = _update(update_id=3, message=_base_message(text="/start"))
    pool = [u1, u2, u3]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            lim = int(_json_body(request)["limit"])
            return httpx.Response(200, json={"ok": True, "result": pool[:lim]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    cfg = PollingRuntimeConfig(max_updates_per_batch=2)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_raw_runtime_bundle(
                "tok",
                base_url="https://ex.invalid/bot/",
                client=ac,
                config=cfg,
            )
            assert b.bundle.config is cfg
            r = await b.bundle.runtime.poll_once(correlation_id=new_correlation_id())
            assert r.raw_received_count == 2

    _run(main())


def test_inner_bundle_uses_default_bridge() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_raw_runtime_bundle("tok", base_url="https://ex.invalid/bot/", client=ac)
            assert b.bundle.bridge is accept_mapping_runtime_update

    _run(main())


def test_one_poll_start_yields_one_send() -> None:
    raw = _update(message=_base_message(text="/start"))
    sends: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [raw]})
        if request.url.path.endswith("/sendMessage"):
            sends.append(request)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_raw_runtime_bundle("tok", base_url="https://ex.invalid/bot/", client=ac)
            r = await b.bundle.runtime.poll_once(correlation_id=new_correlation_id())
            assert r.send_count == 1
            assert len(sends) == 1

    _run(main())


def test_two_polls_same_update_id_replay_second_noop_one_audit() -> None:
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
            b = build_slice1_httpx_raw_runtime_bundle("tok", base_url="https://ex.invalid/bot/", client=ac)
            cid = new_correlation_id()
            r1 = await b.bundle.runtime.poll_once(correlation_id=cid)
            r2 = await b.bundle.runtime.poll_once(correlation_id=cid)
            assert r1.send_count == 1 and r1.noop_count == 0
            assert r2.send_count == 0 and r2.noop_count == 1
            assert len(await b.bundle.composition.audit.recorded_events()) == 1

    _run(main())


def test_aclose_is_idempotent() -> None:
    async def main() -> None:
        transport = _empty_transport()
        async with httpx.AsyncClient(transport=transport) as ac:
            b = build_slice1_httpx_raw_runtime_bundle("tok", base_url="https://ex.invalid/bot/", client=ac)
            await b.aclose()
            await b.aclose()

    _run(main())


def test_owned_client_aclose_once_via_public_bundle_without_external_client() -> None:
    async def main() -> None:
        owned = SimpleNamespace(aclose=AsyncMock())
        with patch(
            "app.runtime.telegram_httpx_raw_client.httpx.AsyncClient",
            return_value=owned,
        ) as ctor:
            bundle = build_slice1_httpx_raw_runtime_bundle("tok", base_url="https://ex.invalid/bot/")
            await bundle.aclose()
            await bundle.aclose()
        ctor.assert_called_once()
        owned.aclose.assert_awaited_once()

    _run(main())
