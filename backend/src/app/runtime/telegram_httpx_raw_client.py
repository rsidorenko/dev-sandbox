"""Telegram Bot API raw client via httpx (getUpdates / sendMessage only)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from app.runtime.polling_policy import (
    DEFAULT_POLLING_POLICY,
    INHERIT_CLIENT_TIMEOUT_MODE,
    LONG_POLL_FETCH_REQUEST,
    ORDINARY_OUTBOUND_REQUEST,
    OVERRIDE_HTTPX_TIMEOUT_MODE,
    PollingPolicy,
    PollingTimeoutDecision,
)

_DEFAULT_OWNED_ASYNC_CLIENT_TIMEOUT = httpx.Timeout(35.0)
_MEDIA_UPLOAD_TIMEOUT = httpx.Timeout(120.0, connect=30.0)


def _default_base_url(bot_token: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}/"


def _normalize_base(url: str) -> str:
    return url if url.endswith("/") else f"{url}/"


def _parse_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("telegram API response is not valid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("telegram API response has invalid shape")
    return data


def _raise_if_not_ok(data: dict[str, Any]) -> None:
    if "ok" not in data:
        raise RuntimeError("telegram API response missing ok field")
    if data["ok"] is not True:
        raise RuntimeError("telegram API error")


def _httpx_post_timeout_kwargs(decision: PollingTimeoutDecision) -> dict[str, httpx.Timeout]:
    if decision.mode == INHERIT_CLIENT_TIMEOUT_MODE:
        return {}
    if decision.mode == OVERRIDE_HTTPX_TIMEOUT_MODE:
        to = decision.httpx_timeout
        if to is None:
            raise RuntimeError("override_httpx_timeout requires httpx_timeout")
        if not isinstance(to, httpx.Timeout):
            raise RuntimeError("polling timeout override must be httpx.Timeout")
        return {"timeout": to}
    raise RuntimeError(f"unsupported polling timeout mode: {decision.mode!r}")


class HttpxTelegramRawPollingClient:
    __slots__ = ("_base", "_client", "_closed", "_owns", "_polling_policy")

    def __init__(
        self,
        bot_token: str,
        *,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        polling_policy: PollingPolicy = DEFAULT_POLLING_POLICY,
    ) -> None:
        if base_url is None:
            self._base = _default_base_url(bot_token)
        else:
            self._base = _normalize_base(base_url)
        if client is None:
            self._client = httpx.AsyncClient(timeout=_DEFAULT_OWNED_ASYNC_CLIENT_TIMEOUT)
            self._owns = True
        else:
            self._client = client
            self._owns = False
        self._closed = False
        self._polling_policy = polling_policy

    @property
    def polling_policy(self) -> PollingPolicy:
        return self._polling_policy

    async def aclose(self) -> None:
        if not self._owns or self._closed:
            return
        self._closed = True
        await self._client.aclose()

    async def fetch_raw_updates(
        self,
        *,
        limit: int,
        offset: int | None = None,
    ) -> Sequence[object]:
        td = self._polling_policy.timeout.timeout_for_request(LONG_POLL_FETCH_REQUEST)
        post_kw = _httpx_post_timeout_kwargs(td)
        body: dict[str, Any] = {"limit": limit, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            body["offset"] = offset
        response = await self._client.post(f"{self._base}getUpdates", json=body, **post_kw)
        response.raise_for_status()
        data = _parse_json_object(response)
        _raise_if_not_ok(data)
        if "result" not in data:
            raise RuntimeError("telegram API response missing result field")
        result = data["result"]
        if not isinstance(result, list):
            raise RuntimeError("telegram API result is not a list")
        out: list[object] = []
        for item in result:
            if not isinstance(item, dict):
                raise RuntimeError("telegram API update item has invalid shape")
            out.append(item)
        return out

    async def set_my_commands(self, commands: Sequence[Mapping[str, str]]) -> None:
        """Call Telegram ``setMyCommands`` to define the bot's native menu commands."""
        td = self._polling_policy.timeout.timeout_for_request(ORDINARY_OUTBOUND_REQUEST)
        post_kw = _httpx_post_timeout_kwargs(td)
        body: dict[str, Any] = {"commands": [dict(c) for c in commands]}
        response = await self._client.post(f"{self._base}setMyCommands", json=body, **post_kw)
        response.raise_for_status()
        data = _parse_json_object(response)
        _raise_if_not_ok(data)

    async def send_text_message(
        self,
        chat_id: int,
        text: str,
        *,
        correlation_id: str,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> int:
        td = self._polling_policy.timeout.timeout_for_request(ORDINARY_OUTBOUND_REQUEST)
        post_kw = _httpx_post_timeout_kwargs(td)
        _ = correlation_id
        body: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode is not None:
            body["parse_mode"] = parse_mode
        if reply_markup is not None:
            body["reply_markup"] = dict(reply_markup)
        if disable_web_page_preview:
            body["disable_web_page_preview"] = True
        response = await self._client.post(f"{self._base}sendMessage", json=body, **post_kw)
        response.raise_for_status()
        data = _parse_json_object(response)
        _raise_if_not_ok(data)
        result = data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("telegram API sendMessage result is not an object")
        mid = result.get("message_id")
        if type(mid) is not int:
            raise RuntimeError("telegram API sendMessage result missing message_id")
        return mid

    async def answer_callback_query(self, callback_query_id: str) -> None:
        """Call Telegram ``answerCallbackQuery`` to dismiss the inline button loading indicator."""
        td = self._polling_policy.timeout.timeout_for_request(ORDINARY_OUTBOUND_REQUEST)
        post_kw = _httpx_post_timeout_kwargs(td)
        body: dict[str, Any] = {"callback_query_id": callback_query_id}
        response = await self._client.post(f"{self._base}answerCallbackQuery", json=body, **post_kw)
        response.raise_for_status()
        data = _parse_json_object(response)
        _raise_if_not_ok(data)

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        """Call Telegram ``deleteMessage`` to remove a message."""
        td = self._polling_policy.timeout.timeout_for_request(ORDINARY_OUTBOUND_REQUEST)
        post_kw = _httpx_post_timeout_kwargs(td)
        body: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        response = await self._client.post(f"{self._base}deleteMessage", json=body, **post_kw)
        response.raise_for_status()
        data = _parse_json_object(response)
        _raise_if_not_ok(data)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> int:
        """Call Telegram ``editMessageText`` to update an existing message in-place."""
        td = self._polling_policy.timeout.timeout_for_request(ORDINARY_OUTBOUND_REQUEST)
        post_kw = _httpx_post_timeout_kwargs(td)
        body: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if parse_mode is not None:
            body["parse_mode"] = parse_mode
        if reply_markup is not None:
            body["reply_markup"] = dict(reply_markup)
        response = await self._client.post(f"{self._base}editMessageText", json=body, **post_kw)
        response.raise_for_status()
        data = _parse_json_object(response)
        _raise_if_not_ok(data)
        return message_id

    async def send_video(
        self,
        chat_id: int,
        video_path: str,
        *,
        caption: str | None = None,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> int:
        """Call Telegram ``sendVideo`` with a local file."""
        td = self._polling_policy.timeout.timeout_for_request(ORDINARY_OUTBOUND_REQUEST)
        post_kw = _httpx_post_timeout_kwargs(td) if td.mode == OVERRIDE_HTTPX_TIMEOUT_MODE else {"timeout": _MEDIA_UPLOAD_TIMEOUT}
        data: dict[str, Any] = {"chat_id": chat_id}
        if caption is not None:
            data["caption"] = caption
        if parse_mode is not None:
            data["parse_mode"] = parse_mode
        if reply_markup is not None:
            data["reply_markup"] = dict(reply_markup)
        with open(video_path, "rb") as vf:
            files = {"video": (video_path.rsplit("/", 1)[-1], vf, "video/mp4")}
            response = await self._client.post(f"{self._base}sendVideo", data=data, files=files, **post_kw)
        response.raise_for_status()
        result_data = _parse_json_object(response)
        _raise_if_not_ok(result_data)
        result = result_data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("telegram API sendVideo result is not an object")
        mid = result.get("message_id")
        if type(mid) is not int:
            raise RuntimeError("telegram API sendVideo result missing message_id")
        return mid

    async def send_photo(
        self,
        chat_id: int,
        photo_path: str,
        *,
        caption: str | None = None,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> int:
        """Call Telegram ``sendPhoto`` with a local file."""
        td = self._polling_policy.timeout.timeout_for_request(ORDINARY_OUTBOUND_REQUEST)
        post_kw = _httpx_post_timeout_kwargs(td) if td.mode == OVERRIDE_HTTPX_TIMEOUT_MODE else {"timeout": _MEDIA_UPLOAD_TIMEOUT}
        data: dict[str, Any] = {"chat_id": chat_id}
        if caption is not None:
            data["caption"] = caption
        if parse_mode is not None:
            data["parse_mode"] = parse_mode
        if reply_markup is not None:
            data["reply_markup"] = dict(reply_markup)
        with open(photo_path, "rb") as pf:
            files = {"photo": (photo_path.rsplit("/", 1)[-1], pf, "image/jpeg")}
            response = await self._client.post(f"{self._base}sendPhoto", data=data, files=files, **post_kw)
        response.raise_for_status()
        result_data = _parse_json_object(response)
        _raise_if_not_ok(result_data)
        result = result_data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("telegram API sendPhoto result is not an object")
        mid = result.get("message_id")
        if type(mid) is not int:
            raise RuntimeError("telegram API sendPhoto result missing message_id")
        return mid

    async def send_document(
        self,
        chat_id: int,
        document_path: str,
        *,
        caption: str | None = None,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> int:
        """Call Telegram ``sendDocument`` with a local file."""
        td = self._polling_policy.timeout.timeout_for_request(ORDINARY_OUTBOUND_REQUEST)
        post_kw = _httpx_post_timeout_kwargs(td) if td.mode == OVERRIDE_HTTPX_TIMEOUT_MODE else {"timeout": _MEDIA_UPLOAD_TIMEOUT}
        data: dict[str, Any] = {"chat_id": chat_id}
        if caption is not None:
            data["caption"] = caption
        if parse_mode is not None:
            data["parse_mode"] = parse_mode
        if reply_markup is not None:
            data["reply_markup"] = dict(reply_markup)
        with open(document_path, "rb") as df:
            files = {"document": (document_path.rsplit("/", 1)[-1], df, "application/octet-stream")}
            response = await self._client.post(f"{self._base}sendDocument", data=data, files=files, **post_kw)
        response.raise_for_status()
        result_data = _parse_json_object(response)
        _raise_if_not_ok(result_data)
        result = result_data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("telegram API sendDocument result is not an object")
        mid = result.get("message_id")
        if type(mid) is not int:
            raise RuntimeError("telegram API sendDocument result missing message_id")
        return mid
