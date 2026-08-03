"""Tests for CryptoBot webhook signature verification and paid_at parsing.

These cover the provider-specific layer (HMAC-SHA256 over the raw body, fail-closed) and
the timestamp parsing helper. The shared fulfillment logic invoked downstream is already
covered by the YooKassa webhook tests, since both providers route through the same
``payment_webhook_helpers`` functions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

from app.cryptobot.payload import encode_payment_context
from app.cryptobot.webhook import _parse_paid_at, verify_cryptobot_signature

_API_TOKEN = "5tQpQeeper3456789:uECC7Cvr4567890b1234S67b89F12g34"


def _sign(body: bytes, api_token: str = _API_TOKEN) -> str:
    secret = hashlib.sha256(api_token.encode("utf-8")).digest()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


# ── signature verification ────────────────────────────────────────────────────


def test_signature_valid_for_real_body() -> None:
    body = json.dumps({"update_id": 1, "update_type": "invoice_paid", "payload": {"invoice_id": "i1"}}).encode()
    assert verify_cryptobot_signature(api_token=_API_TOKEN, raw_body=body, signature_header=_sign(body)) is True


def test_signature_rejects_tampered_body() -> None:
    body = b'{"update_id":1,"update_type":"invoice_paid"}'
    tampered = b'{"update_id":2,"update_type":"invoice_paid"}'
    assert verify_cryptobot_signature(api_token=_API_TOKEN, raw_body=tampered, signature_header=_sign(body)) is False


def test_signature_rejects_wrong_token() -> None:
    body = b'{"update_id":1}'
    assert verify_cryptobot_signature(api_token="wrong-token", raw_body=body, signature_header=_sign(body)) is False


def test_signature_rejects_missing_header_or_token() -> None:
    body = b'{"update_id":1}'
    assert verify_cryptobot_signature(api_token=_API_TOKEN, raw_body=body, signature_header=None) is False
    assert verify_cryptobot_signature(api_token="", raw_body=body, signature_header=_sign(body)) is False


def test_signature_accepts_both_documented_schemes() -> None:
    # Diagnostic phase: Crypto Pay docs are ambiguous (sha256-of-token vs raw token as
    # HMAC key). Accept both until the live webhook confirms which one CryptoBot uses,
    # then tighten. An attacker still needs the API token to forge either.
    body = b"payload-bytes"
    raw_token_sig = hmac.new(_API_TOKEN.encode(), body, hashlib.sha256).hexdigest()
    sha_token_sig = hmac.new(hashlib.sha256(_API_TOKEN.encode()).digest(), body, hashlib.sha256).hexdigest()
    assert verify_cryptobot_signature(api_token=_API_TOKEN, raw_body=body, signature_header=sha_token_sig) is True
    assert verify_cryptobot_signature(api_token=_API_TOKEN, raw_body=body, signature_header=raw_token_sig) is True


# ── paid_at parsing ───────────────────────────────────────────────────────────


def test_parse_paid_at_z_suffix() -> None:
    dt = _parse_paid_at("2026-08-03T12:34:56Z")
    assert dt.tzinfo is not None
    assert dt == datetime(2026, 8, 3, 12, 34, 56, tzinfo=UTC)


def test_parse_paid_at_explicit_offset() -> None:
    dt = _parse_paid_at("2026-08-03T15:34:56+03:00")
    assert dt == datetime(2026, 8, 3, 12, 34, 56, tzinfo=UTC)


def test_parse_paid_at_fallback_to_now_on_garbage() -> None:
    before = datetime.now(UTC)
    dt = _parse_paid_at("not-a-date")
    after = datetime.now(UTC)
    assert before <= dt <= after


def test_parse_paid_at_fallback_on_missing() -> None:
    before = datetime.now(UTC)
    dt = _parse_paid_at(None)
    after = datetime.now(UTC)
    assert before <= dt <= after


# ── payload context survives a full webhook round-trip ────────────────────────


def test_payment_context_roundtrips_through_invoice_payload() -> None:
    # Creation encodes the context into the invoice payload; the webhook decodes it back to
    # the same metadata dict shape the shared fulfillment helpers consume.
    context = {"plan_id": "1m", "device_count": "5", "telegram_user_id": "999", "internal_user_id": "u999"}
    payload_str = encode_payment_context(context)
    assert isinstance(payload_str, str)
    from app.cryptobot.payload import decode_payment_context

    assert decode_payment_context(payload_str) == context
