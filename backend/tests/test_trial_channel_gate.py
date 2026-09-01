"""Тесты гейта подписки на канал: парсинг getChatMember, env-флаги, fail-open."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from app.application.trial_channel_gate import (
    DEFAULT_CHANNEL_USERNAME,
    TRIAL_CHANNEL_SUBSCRIBE_ENABLE,
    TRIAL_CHANNEL_USERNAME,
    TelegramChannelMembershipChecker,
    channel_username_from_env,
    trial_channel_gate_enabled_from_env,
)
from app.shared.test_helpers import run_async as _run


def _make_checker(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    channel: str = "bravada_vpn",
) -> TelegramChannelMembershipChecker:
    return TelegramChannelMembershipChecker(
        "123:ABC", channel, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def _check(
    checker: TelegramChannelMembershipChecker,
    user_id: int = 42,
) -> bool | None:
    try:
        return await checker.is_member(user_id)
    finally:
        await checker.aclose()


def _member_response(status: str, *, is_member: bool | None = None) -> dict:
    result: dict = {"status": status, "user": {"id": 42}}
    if is_member is not None:
        result["is_member"] = is_member
    return {"ok": True, "result": result}


def test_env_flag_off_by_default() -> None:
    assert trial_channel_gate_enabled_from_env({}) is False
    assert trial_channel_gate_enabled_from_env({TRIAL_CHANNEL_SUBSCRIBE_ENABLE: "0"}) is False
    assert trial_channel_gate_enabled_from_env({TRIAL_CHANNEL_SUBSCRIBE_ENABLE: ""}) is False


def test_env_flag_on_values() -> None:
    for raw in ("1", "true", "yes", "TRUE", " Yes "):
        assert trial_channel_gate_enabled_from_env({TRIAL_CHANNEL_SUBSCRIBE_ENABLE: raw}) is True


def test_channel_username_default_and_stripping() -> None:
    assert channel_username_from_env({}) == DEFAULT_CHANNEL_USERNAME == "bravada_vpn"
    assert channel_username_from_env({TRIAL_CHANNEL_USERNAME: "@my_channel"}) == "my_channel"
    assert channel_username_from_env({TRIAL_CHANNEL_USERNAME: "  "}) == DEFAULT_CHANNEL_USERNAME


def test_is_member_true_for_member_statuses() -> None:
    for status in ("creator", "administrator", "member"):
        checker = _make_checker(lambda req, s=status: httpx.Response(200, json=_member_response(s)))
        assert _run(_check(checker, user_id=7)) is True


def test_is_member_restricted_follows_is_member_flag() -> None:
    for flag, expected in ((True, True), (False, False)):
        checker = _make_checker(
            lambda req, f=flag: httpx.Response(200, json=_member_response("restricted", is_member=f))
        )
        assert _run(_check(checker)) is expected


def test_is_member_false_for_left_kicked() -> None:
    for status in ("left", "kicked"):
        checker = _make_checker(lambda req, s=status: httpx.Response(200, json=_member_response(s)))
        assert _run(_check(checker)) is False


def test_is_member_sends_channel_and_user() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_member_response("member"))

    checker = _make_checker(handler, channel="@bravada_vpn")
    assert _run(_check(checker, user_id=777)) is True
    assert captured["url"].endswith("/getChatMember")
    assert captured["body"] == {"chat_id": "@bravada_vpn", "user_id": 777}


def test_is_member_fail_open_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    checker = _make_checker(handler)
    assert _run(_check(checker)) is None


def test_is_member_fail_open_on_not_ok() -> None:
    # Бот не админ канала / канал не найден — Telegram вернёт ok=false.
    checker = _make_checker(
        lambda req: httpx.Response(
            200, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        )
    )
    assert _run(_check(checker)) is None


def test_is_member_fail_open_on_http_500() -> None:
    checker = _make_checker(lambda req: httpx.Response(500, json={"ok": False}))
    assert _run(_check(checker)) is None


def test_is_member_fail_open_on_bad_json_and_shape() -> None:
    checker_bad_json = _make_checker(lambda req: httpx.Response(200, content=b"not-json"))
    assert _run(_check(checker_bad_json)) is None

    checker_bad_shape = _make_checker(lambda req: httpx.Response(200, json={"ok": True, "result": "weird"}))
    assert _run(_check(checker_bad_shape)) is None
