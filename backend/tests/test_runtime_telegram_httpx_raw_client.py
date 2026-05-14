"""Unit tests for :mod:`app.runtime.telegram_httpx_raw_client` (no network)."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import replace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.runtime import telegram_httpx_raw_client
from app.runtime.polling_policy import (
    DEFAULT_POLLING_POLICY,
    INHERIT_CLIENT_TIMEOUT_MODE,
    LONG_POLL_FETCH_REQUEST,
    ORDINARY_OUTBOUND_REQUEST,
    OVERRIDE_HTTPX_TIMEOUT_MODE,
    PollingTimeoutDecision,
    create_default_polling_policy,
)
from app.runtime.telegram_httpx_raw_client import HttpxTelegramRawPollingClient


def _json_body(request: httpx.Request) -> dict:
    if not request.content:
        return {}
    return json.loads(request.content.decode())


class _TimeoutPolicySpy:
    kind = "spy"

    def __init__(self) -> None:
        self.request_kinds: list[str] = []
        self.decisions: list[PollingTimeoutDecision] = []

    def timeout_for_request(self, request_kind: str) -> PollingTimeoutDecision:
        self.request_kinds.append(request_kind)
        decision = PollingTimeoutDecision(request_kind=request_kind)
        self.decisions.append(decision)
        return decision


def test_default_polling_policy_is_module_default() -> None:
    async def main() -> None:
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "result": []}))
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            assert c.polling_policy is DEFAULT_POLLING_POLICY

    asyncio.run(main())


def test_custom_polling_policy_stored_by_identity() -> None:
    custom = create_default_polling_policy()

    async def main() -> None:
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "result": []}))
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient(
                "x",
                base_url="https://e/b/",
                client=ac,
                polling_policy=custom,
            )
            assert c.polling_policy is custom

    asyncio.run(main())


def test_fetch_raw_updates_behavior_unchanged_with_custom_polling_policy() -> None:
    captured: list[httpx.Request] = []
    custom = create_default_polling_policy()

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True, "result": []})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient(
                "x",
                base_url="https://example.invalid/bot/",
                client=ac,
                polling_policy=custom,
            )
            await c.fetch_raw_updates(limit=42)

    asyncio.run(main())
    assert len(captured) == 1
    assert captured[0].url.path.endswith("/getUpdates")
    body = _json_body(captured[0])
    assert body["limit"] == 42
    assert "offset" not in body


def test_timeout_policy_lookup_request_kinds_and_happy_path_bodies_unchanged() -> None:
    spy = _TimeoutPolicySpy()
    polling_policy = replace(create_default_polling_policy(), timeout=spy)
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient(
                "x",
                base_url="https://e/b/",
                client=ac,
                polling_policy=polling_policy,
            )
            await c.fetch_raw_updates(limit=42)
            await c.send_text_message(7, "hi", correlation_id="cid-abc")

    asyncio.run(main())
    assert spy.request_kinds == [LONG_POLL_FETCH_REQUEST, ORDINARY_OUTBOUND_REQUEST]
    assert len(spy.decisions) == 2
    assert spy.decisions[0].request_kind == LONG_POLL_FETCH_REQUEST
    assert spy.decisions[0].mode == INHERIT_CLIENT_TIMEOUT_MODE
    assert spy.decisions[1].request_kind == ORDINARY_OUTBOUND_REQUEST
    assert spy.decisions[1].mode == INHERIT_CLIENT_TIMEOUT_MODE
    assert len(captured) == 2
    assert captured[0].url.path.endswith("/getUpdates")
    assert _json_body(captured[0]) == {"limit": 42, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
    assert "offset" not in _json_body(captured[0])
    assert captured[1].url.path.endswith("/sendMessage")
    assert _json_body(captured[1]) == {"chat_id": 7, "text": "hi"}


def test_inherit_timeout_mode_post_has_no_per_request_timeout_kwarg() -> None:
    post_kwargs: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(200, json={"ok": True, "result": []})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            orig_post = ac.post

            async def post_wrap(*args: Any, **kwargs: Any) -> httpx.Response:
                post_kwargs.append(kwargs)
                return await orig_post(*args, **kwargs)

            ac.post = post_wrap  # type: ignore[method-assign]
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            await c.fetch_raw_updates(limit=1)
            await c.send_text_message(1, "x", correlation_id="c")

    asyncio.run(main())
    assert len(post_kwargs) == 2
    assert "timeout" not in post_kwargs[0]
    assert "timeout" not in post_kwargs[1]


def test_override_httpx_timeout_mode_passes_timeout_to_post() -> None:
    t = httpx.Timeout(1.0)
    post_kwargs: list[dict[str, Any]] = []

    class FixedTimeout:
        kind = "fixed"

        def timeout_for_request(self, request_kind: str) -> PollingTimeoutDecision:
            return PollingTimeoutDecision(
                request_kind=request_kind,
                mode=OVERRIDE_HTTPX_TIMEOUT_MODE,
                httpx_timeout=t,
            )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": []})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            orig_post = ac.post

            async def post_wrap(*args: Any, **kwargs: Any) -> httpx.Response:
                post_kwargs.append(kwargs)
                return await orig_post(*args, **kwargs)

            ac.post = post_wrap  # type: ignore[method-assign]
            policy = replace(create_default_polling_policy(), timeout=FixedTimeout())
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac, polling_policy=policy)
            await c.fetch_raw_updates(limit=1)

    asyncio.run(main())
    assert len(post_kwargs) == 1
    assert post_kwargs[0]["timeout"] is t


def test_override_httpx_timeout_mode_missing_payload_raises_before_http() -> None:
    class MissingPayload:
        kind = "missing"

        def timeout_for_request(self, request_kind: str) -> PollingTimeoutDecision:
            return PollingTimeoutDecision(request_kind=request_kind, mode=OVERRIDE_HTTPX_TIMEOUT_MODE)

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True, "result": []})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            policy = replace(create_default_polling_policy(), timeout=MissingPayload())
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac, polling_policy=policy)
            with pytest.raises(RuntimeError, match="override_httpx_timeout requires httpx_timeout"):
                await c.fetch_raw_updates(limit=1)

    asyncio.run(main())
    assert calls == 0


def test_override_httpx_timeout_mode_invalid_payload_raises_before_http() -> None:
    class BadPayload:
        kind = "bad_payload"

        def timeout_for_request(self, request_kind: str) -> PollingTimeoutDecision:
            return PollingTimeoutDecision(
                request_kind=request_kind,
                mode=OVERRIDE_HTTPX_TIMEOUT_MODE,
                httpx_timeout=cast("Any", 3.14),
            )

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True, "result": []})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            policy = replace(create_default_polling_policy(), timeout=BadPayload())
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac, polling_policy=policy)
            with pytest.raises(RuntimeError, match=r"polling timeout override must be httpx\.Timeout"):
                await c.fetch_raw_updates(limit=1)

    asyncio.run(main())
    assert calls == 0


def test_unsupported_polling_timeout_mode_raises_before_http() -> None:
    class BadTimeout:
        kind = "bad"

        def timeout_for_request(self, request_kind: str) -> PollingTimeoutDecision:
            return PollingTimeoutDecision(request_kind=request_kind, mode=cast("Any", "not_supported"))

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True, "result": []})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            policy = replace(create_default_polling_policy(), timeout=BadTimeout())
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac, polling_policy=policy)
            with pytest.raises(RuntimeError, match="unsupported polling timeout mode"):
                await c.fetch_raw_updates(limit=1)

    asyncio.run(main())
    assert calls == 0


def test_fetch_raw_updates_calls_getupdates_with_limit() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True, "result": []})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            client = HttpxTelegramRawPollingClient(
                "x",
                base_url="https://example.invalid/bot/",
                client=ac,
            )
            await client.fetch_raw_updates(limit=42)

    asyncio.run(main())
    assert len(captured) == 1
    assert captured[0].url.path.endswith("/getUpdates")
    body = _json_body(captured[0])
    assert body["limit"] == 42
    assert "offset" not in body


def test_custom_base_url_without_trailing_slash_normalizes_paths() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient(
                "x",
                base_url="https://example.invalid/bot",
                client=ac,
            )
            await c.fetch_raw_updates(limit=1)
            await c.send_text_message(1, "x", correlation_id="c")

    asyncio.run(main())
    assert paths == ["/bot/getUpdates", "/bot/sendMessage"]


@pytest.mark.parametrize("offset", (0, 99))
def test_fetch_raw_updates_passes_offset_when_set(offset: int) -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(_json_body(request))
        return httpx.Response(200, json={"ok": True, "result": []})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            await c.fetch_raw_updates(limit=1, offset=offset)

    asyncio.run(main())
    assert bodies[0]["offset"] == offset
    assert bodies[0]["limit"] == 1


def test_fetch_raw_updates_returns_sequence_of_dicts() -> None:
    updates = [{"update_id": 1, "message": {"message_id": 1}}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": updates})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            out = await c.fetch_raw_updates(limit=10)
        assert list(out) == updates
        assert all(isinstance(u, dict) for u in out)

    asyncio.run(main())


def test_send_text_message_calls_sendmessage_minimal_body() -> None:
    requests_log: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            await c.send_text_message(7, "hi", correlation_id="cid-abc")

    asyncio.run(main())
    assert len(requests_log) == 1
    assert requests_log[0].url.path.endswith("/sendMessage")
    body = _json_body(requests_log[0])
    assert body == {"chat_id": 7, "text": "hi"}


def test_send_text_message_includes_reply_markup_only_when_present() -> None:
    requests_log: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            await c.send_text_message(7, "plain", correlation_id="cid-1")
            await c.send_text_message(
                8,
                "with kb",
                correlation_id="cid-2",
                reply_markup={"keyboard": [["/menu"]], "resize_keyboard": True},
            )

    asyncio.run(main())
    body0 = _json_body(requests_log[0])
    body1 = _json_body(requests_log[1])
    assert "reply_markup" not in body0
    assert body1["reply_markup"] == {"keyboard": [["/menu"]], "resize_keyboard": True}


def test_correlation_id_not_in_outbound_request() -> None:
    raw_content: bytes = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal raw_content
        raw_content = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            await c.send_text_message(1, "t", correlation_id="secret-corr")

    asyncio.run(main())
    assert b"secret-corr" not in raw_content
    assert b"correlation" not in raw_content.lower()


@pytest.mark.parametrize(
    "payload",
    (
        {"ok": False, "description": "bad"},
        {"ok": True},
    ),
)
def test_fetch_ok_false_or_missing_result_raises(payload: dict) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(RuntimeError):
                await c.fetch_raw_updates(limit=1)

    asyncio.run(main())


def test_fetch_raw_updates_non_json_body_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(RuntimeError, match="telegram API response is not valid JSON"):
                await c.fetch_raw_updates(limit=1)

    asyncio.run(main())


def test_fetch_raw_updates_json_missing_ok_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(RuntimeError, match="telegram API response missing ok field"):
                await c.fetch_raw_updates(limit=1)

    asyncio.run(main())


def test_fetch_raw_updates_json_not_object_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1])

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(RuntimeError, match="telegram API response has invalid shape"):
                await c.fetch_raw_updates(limit=1)

    asyncio.run(main())


def test_fetch_raw_updates_result_not_list_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": "bad"})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(RuntimeError, match="telegram API result is not a list"):
                await c.fetch_raw_updates(limit=1)

    asyncio.run(main())


def test_fetch_raw_updates_result_item_not_dict_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": [123]})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(RuntimeError, match="telegram API update item has invalid shape"):
                await c.fetch_raw_updates(limit=1)

    asyncio.run(main())


def test_fetch_raw_updates_http_5xx_raises_http_status_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"not json")

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(httpx.HTTPStatusError):
                await c.fetch_raw_updates(limit=1)

    asyncio.run(main())


def test_send_ok_false_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "nope"})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(RuntimeError, match="telegram API error"):
                await c.send_text_message(1, "x", correlation_id="c")

    asyncio.run(main())


def test_send_text_message_non_json_body_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(RuntimeError, match="telegram API response is not valid JSON"):
                await c.send_text_message(1, "x", correlation_id="c")

    asyncio.run(main())


def test_send_text_message_json_missing_ok_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(RuntimeError, match="telegram API response missing ok field"):
                await c.send_text_message(1, "x", correlation_id="c")

    asyncio.run(main())


def test_send_text_message_json_not_object_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1])

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(RuntimeError, match="telegram API response has invalid shape"):
                await c.send_text_message(1, "x", correlation_id="c")

    asyncio.run(main())


def test_send_text_message_http_5xx_raises_http_status_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"not json")

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            with pytest.raises(httpx.HTTPStatusError):
                await c.send_text_message(1, "x", correlation_id="c")

    asyncio.run(main())


def test_aclose_idempotent_and_safe() -> None:
    async def main() -> None:
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "result": []}))
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
            await c.aclose()
            await c.aclose()

    asyncio.run(main())


def test_aclose_owned_client_idempotent() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "result": []}))
    inner = httpx.AsyncClient(transport=transport)

    def _make_client(*_a, **_kw):
        return inner

    async def use_client() -> None:
        c = HttpxTelegramRawPollingClient("x", base_url="https://e/b/")
        await c.aclose()
        await c.aclose()

    with patch("app.runtime.telegram_httpx_raw_client.httpx.AsyncClient", side_effect=_make_client):
        asyncio.run(use_client())
    assert inner.is_closed


def test_owned_async_client_gets_explicit_timeout() -> None:
    mock_instance = MagicMock()
    with patch(
        "app.runtime.telegram_httpx_raw_client.httpx.AsyncClient",
        return_value=mock_instance,
    ) as mock_ac:
        HttpxTelegramRawPollingClient("x", base_url="https://e/b/")
    mock_ac.assert_called_once()
    assert mock_ac.call_args.kwargs.get("timeout") is telegram_httpx_raw_client._DEFAULT_OWNED_ASYNC_CLIENT_TIMEOUT


def test_external_client_skips_async_client_constructor() -> None:
    async def main() -> None:
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "result": []}))
        async with httpx.AsyncClient(transport=transport) as ac:
            with patch("app.runtime.telegram_httpx_raw_client.httpx.AsyncClient") as mock_ac:
                HttpxTelegramRawPollingClient("x", base_url="https://e/b/", client=ac)
                mock_ac.assert_not_called()

    asyncio.run(main())


def test_module_source_excludes_forbidden_substrings() -> None:
    import app.runtime.telegram_httpx_raw_client as mod

    src = inspect.getsource(mod)
    lower = src.lower()
    for word in ("billing", "issuance", "admin", "webhook"):
        assert word not in lower


def test_default_base_url_uses_token_path() -> None:
    paths: list[str] = []
    token = "".join(("M", "Y", "T", "O", "K", "E", "N"))

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "result": []})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as ac:
            c = HttpxTelegramRawPollingClient(token, client=ac)
            await c.fetch_raw_updates(limit=1)

    asyncio.run(main())
    assert f"bot{token}" in paths[0]
    assert paths[0].endswith("/getUpdates")
