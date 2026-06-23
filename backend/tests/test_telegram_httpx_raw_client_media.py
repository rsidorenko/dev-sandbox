"""Tests for HttpxTelegramRawPollingClient media upload — real httpx multipart encoding.

Regression guard: attaching an inline keyboard (reply_markup) to a media send must not
break the multipart upload. Previously ``reply_markup`` was passed as a nested dict, which
made httpx raise ``TypeError: Invalid type for value ... got <class 'dict'>`` and every
captioned media send fall back to a text-only message (no media in the bot's instructions).
The fake in-memory client used by the polling tests never exercises real multipart encoding.
"""

from __future__ import annotations

import json

import httpx

from app.runtime.telegram_httpx_raw_client import HttpxTelegramRawPollingClient
from app.shared.test_helpers import run_async as _run

_KEYBOARD = {"inline_keyboard": [[{"text": "Готово", "callback_data": "next"}]]}


def _ok(result_key: str) -> dict:
    return {"ok": True, "result": {"message_id": 7, result_key: {"file_id": "FILE_ID"}}}


def _extract_form_field(content: bytes, name: str) -> str | None:
    """Extract a multipart form field's raw value from the request body bytes."""
    text = content.decode("utf-8", "replace")
    idx = text.find(f'name="{name}"')
    if idx == -1:
        return None
    start = text.find("\r\n\r\n", idx)
    if start == -1:
        return None
    start += 4
    end = text.find("\r\n--", start)
    return text[start:end] if end != -1 else None


def test_send_video_with_keyboard_serializes_reply_markup_as_json(tmp_path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"FAKE_MP4_BYTES")
    captured: list[httpx.Request] = []

    async def main() -> int:
        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=_ok("video"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = HttpxTelegramRawPollingClient("123:token", client=http_client)
            return await client.send_video(
                42, str(video), caption="📱 Шаг 2 из 6", reply_markup=_KEYBOARD, parse_mode="HTML"
            )

    msg_id = _run(main())
    assert msg_id == 7
    assert len(captured) == 1
    body = captured[0].content
    # reply_markup is present and is valid JSON (not a Python repr of a dict)
    rm = _extract_form_field(body, "reply_markup")
    assert rm is not None
    assert json.loads(rm) == _KEYBOARD
    # scalar fields pass through unchanged
    assert _extract_form_field(body, "caption") == "📱 Шаг 2 из 6"
    assert _extract_form_field(body, "parse_mode") == "HTML"


def test_send_photo_with_keyboard_serializes_reply_markup_as_json(tmp_path) -> None:
    photo = tmp_path / "shot.jpg"
    photo.write_bytes(b"FAKE_JPG_BYTES")
    captured: list[httpx.Request] = []

    async def main() -> int:
        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=_ok("photo"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = HttpxTelegramRawPollingClient("123:token", client=http_client)
            return await client.send_photo(42, str(photo), caption="Шаг 4", reply_markup=_KEYBOARD, parse_mode="HTML")

    msg_id = _run(main())
    assert msg_id == 7
    rm = _extract_form_field(captured[0].content, "reply_markup")
    assert rm is not None
    assert json.loads(rm) == _KEYBOARD


def test_send_document_with_keyboard_serializes_reply_markup_as_json(tmp_path) -> None:
    doc = tmp_path / "karing.exe"
    doc.write_bytes(b"FAKE_EXE_BYTES")
    captured: list[httpx.Request] = []

    async def main() -> int:
        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=_ok("document"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = HttpxTelegramRawPollingClient("123:token", client=http_client)
            return await client.send_document(42, str(doc), caption="Шаг 1", reply_markup=_KEYBOARD)

    msg_id = _run(main())
    assert msg_id == 7
    rm = _extract_form_field(captured[0].content, "reply_markup")
    assert rm is not None
    assert json.loads(rm) == _KEYBOARD


def test_multipart_form_fields_helper_primitive_and_complex() -> None:
    from app.runtime.telegram_httpx_raw_client import _multipart_form_fields

    out = _multipart_form_fields({"chat_id": 42, "caption": "x", "parse_mode": None, "reply_markup": _KEYBOARD})
    assert out["chat_id"] == 42
    assert out["caption"] == "x"
    assert out["parse_mode"] is None
    assert json.loads(out["reply_markup"]) == _KEYBOARD
