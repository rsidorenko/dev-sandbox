"""Tests for the CryptoBot payload codec and Crypto Pay API client.

The payload codec round-trips the payment context through CryptoBot's single ``payload``
string (the crypto analog of YooKassa's ``metadata`` dict). The client is exercised via an
injected fake httpx client so no network is involved.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from app.cryptobot.client import CryptoBotClient, CryptoBotError, _invoice_to_info
from app.cryptobot.payload import decode_payment_context, encode_payment_context


def _run(coro):
    return asyncio.run(coro)


# ── payload codec ──────────────────────────────────────────────────────────────


def test_encode_decode_roundtrip_preserves_all_fields() -> None:
    ctx = {
        "plan_id": "1m",
        "device_count": "7",
        "telegram_user_id": "123456",
        "internal_user_id": "u123456",
        "kind": "add_device",
        "new_device_count": "7",
        "expected_amount_kopecks": "16000",
        "discount_kopecks": "500",
        "promo_code": "NEW10",
    }
    assert decode_payment_context(encode_payment_context(ctx)) == ctx


def test_encode_decode_handles_unicode_and_special_chars() -> None:
    ctx = {"promo_code": "ПРИВЕТ-2026", "note": "a:b|c,d"}
    assert decode_payment_context(encode_payment_context(ctx)) == {k: str(v) for k, v in ctx.items()}


def test_decode_missing_or_blank_returns_empty_dict() -> None:
    assert decode_payment_context(None) == {}
    assert decode_payment_context("") == {}


def test_decode_garbage_returns_empty_dict() -> None:
    assert decode_payment_context("not-base64-or-json!!!") == {}
    # A valid base64 of a JSON *list* (not a dict) → empty dict.
    list_payload = base64.urlsafe_b64encode(json.dumps([1, 2, 3]).encode()).decode().rstrip("=")
    assert decode_payment_context(list_payload) == {}


def test_decode_coerces_non_string_values_to_str() -> None:
    # The shared webhook helpers expect metadata: dict[str, str]; numeric values must stringify.
    payload = base64.urlsafe_b64encode(json.dumps({"device_count": 7}).encode()).decode().rstrip("=")
    assert decode_payment_context(payload) == {"device_count": "7"}


# ── client (fake httpx) ────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, payload: object, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *responses: _FakeResponse) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, object, dict[str, str]]] = []

    async def post(self, url: str, json: object = None, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.calls.append((url, json, headers or {}))
        return self._responses.pop(0)


def test_create_invoice_success_returns_id_and_url() -> None:
    fake = _FakeAsyncClient(
        _FakeResponse(
            200, {"ok": True, "result": {"invoice_id": "inv_42", "mini_app_url": "https://t.me/CryptoBot?start=inv_42"}}
        )
    )
    res = _run(
        CryptoBotClient(api_token="tok", http_client=fake).create_invoice(
            amount_rubles=249,
            payload_str="payload-abc",
            description="Bravada VPN - 1m",
            paid_btn_url="https://bravada-connect.ru/dashboard",
            expires_in=3600,
        )
    )
    assert res.invoice_id == "inv_42"
    assert res.pay_url == "https://t.me/CryptoBot?start=inv_42"
    url, body, headers = fake.calls[0]
    assert url.endswith("/createInvoice")
    assert body["currency_type"] == "fiat"
    assert body["fiat"] == "RUB"
    assert body["amount"] == "249"
    assert body["payload"] == "payload-abc"
    assert headers["Crypto-Pay-API-Token"] == "tok"


def test_create_invoice_falls_back_to_bot_invoice_url() -> None:
    fake = _FakeAsyncClient(
        _FakeResponse(
            200, {"ok": True, "result": {"invoice_id": "i1", "bot_invoice_url": "https://t.me/CryptoBot?start=i1"}}
        )
    )
    res = _run(
        CryptoBotClient(api_token="t", http_client=fake).create_invoice(
            amount_rubles=10, payload_str="p", description="d", paid_btn_url="u", expires_in=60
        )
    )
    assert res.pay_url == "https://t.me/CryptoBot?start=i1"


def test_create_invoice_missing_fields_raises() -> None:
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": {"invoice_id": ""}}))
    with pytest.raises(CryptoBotError):
        _run(
            CryptoBotClient(api_token="t", http_client=fake).create_invoice(
                amount_rubles=10, payload_str="p", description="d", paid_btn_url="u", expires_in=60
            )
        )


def test_create_invoice_non_ok_raises() -> None:
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": False, "error": "some_error"}))
    with pytest.raises(CryptoBotError):
        _run(
            CryptoBotClient(api_token="t", http_client=fake).create_invoice(
                amount_rubles=10, payload_str="p", description="d", paid_btn_url="u", expires_in=60
            )
        )


def test_get_invoice_returns_parsed_info() -> None:
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "ok": True,
                "result": {
                    "items": [
                        {
                            "invoice_id": "i9",
                            "status": "paid",
                            "amount": "3.5",
                            "fiat": "RUB",
                            "fiat_amount": "249",
                            "payload": "ctx",
                        }
                    ]
                },
            },
        )
    )
    info = _run(CryptoBotClient(api_token="t", http_client=fake).get_invoice("i9"))
    assert info is not None
    assert info.invoice_id == "i9"
    assert info.status == "paid"
    assert info.fiat == "RUB"
    assert info.fiat_amount == "249"
    assert info.payload == "ctx"


def test_get_invoice_empty_items_returns_none() -> None:
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": {"items": []}}))
    assert _run(CryptoBotClient(api_token="t", http_client=fake).get_invoice("missing")) is None


def test_get_invoice_api_error_returns_none() -> None:
    fake = _FakeAsyncClient(_FakeResponse(400, {}, text="bad request"))
    # A 4xx raises CryptoBotError inside _post -> get_invoice swallows it and returns None.
    assert _run(CryptoBotClient(api_token="t", http_client=fake).get_invoice("x")) is None


def test_invoice_to_info_defaults_missing_fields() -> None:
    info = _invoice_to_info({"invoice_id": "i", "status": "active"})
    assert info.invoice_id == "i"
    assert info.status == "active"
    assert info.fiat == ""
    assert info.fiat_amount == "0"
    assert info.payload == ""
