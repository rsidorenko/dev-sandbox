"""Tests for YooKassa webhook source verification (IP-based, per YooKassa docs).

YooKassa does NOT send an HMAC signature header; authenticity is verified by
source IP (+ object-status re-fetch via the API). The prior signature check
rejected every real notification and broke all payments — these tests lock in
the IP-based verification that replaced it.
"""

from __future__ import annotations

import os
import sys

from app.yookassa.webhook import (
    _client_ip,
    _is_yookassa_ip,
    _verify_yookassa_source,
)


class _FakeClient:
    def __init__(self, host: str | None) -> None:
        self.host = host


class _FakeRequest:
    """Minimal stand-in for starlette.Request for the verification helpers."""

    def __init__(self, headers: dict[str, str] | None = None, client_host: str | None = None) -> None:
        self.headers = headers or {}
        self.client = _FakeClient(client_host) if client_host is not None else None


# ── _is_yookassa_ip ──────────────────────────────────────────────────────────


def test_is_yookassa_ip_recognizes_documented_ranges() -> None:
    assert _is_yookassa_ip("185.71.76.10") is True      # 185.71.76.0/27
    assert _is_yookassa_ip("185.71.77.5") is True       # 185.71.77.0/27
    assert _is_yookassa_ip("77.75.153.10") is True      # 77.75.153.0/25
    assert _is_yookassa_ip("77.75.156.11") is True      # /32
    assert _is_yookassa_ip("77.75.156.35") is True      # /32
    assert _is_yookassa_ip("77.75.154.200") is True     # 77.75.154.128/25
    assert _is_yookassa_ip("2a02:5180::1") is True      # 2a02:5180::/32


def test_is_yookassa_ip_rejects_non_yookassa() -> None:
    assert _is_yookassa_ip("8.8.8.8") is False
    assert _is_yookassa_ip("172.18.0.6") is False       # nginx docker IP (not YooKassa)
    assert _is_yookassa_ip("127.0.0.1") is False
    assert _is_yookassa_ip("not-an-ip") is False
    assert _is_yookassa_ip("") is False


# ── _client_ip ───────────────────────────────────────────────────────────────


def test_client_ip_prefers_x_forwarded_for_leftmost() -> None:
    req = _FakeRequest({"x-forwarded-for": "185.71.76.10, 10.0.0.1"}, client_host="172.18.0.6")
    assert _client_ip(req) == "185.71.76.10"


def test_client_ip_falls_back_to_x_real_ip() -> None:
    req = _FakeRequest({"x-real-ip": "77.75.156.35"}, client_host="172.18.0.6")
    assert _client_ip(req) == "77.75.156.35"


def test_client_ip_falls_back_to_connection_host() -> None:
    req = _FakeRequest({}, client_host="172.18.0.6")
    assert _client_ip(req) == "172.18.0.6"


def test_client_ip_none_when_nothing_available() -> None:
    req = _FakeRequest({}, client_host=None)
    assert _client_ip(req) is None


# ── _verify_yookassa_source ──────────────────────────────────────────────────


def test_source_accepts_yookassa_ip_via_xff() -> None:
    req = _FakeRequest({"x-forwarded-for": "185.71.76.10"})
    assert _verify_yookassa_source(req) is None


def test_source_rejects_non_yookassa_ip_when_forwarded() -> None:
    # A forwarded IP is present but it's not a YooKassa range -> reject (401).
    req = _FakeRequest({"x-forwarded-for": "8.8.8.8"})
    resp = _verify_yookassa_source(req)
    assert resp is not None
    assert resp.status_code == 401


def test_source_rejects_spoofed_xff_from_local_range() -> None:
    req = _FakeRequest({"x-forwarded-for": "172.18.0.6"})
    resp = _verify_yookassa_source(req)
    assert resp is not None
    assert resp.status_code == 401


def test_source_relies_on_api_when_no_forwarded_header() -> None:
    # No XFF/X-Real-IP at all (e.g. proxy didn't set it) -> proceed, rely on the
    # authoritative API re-fetch rather than rejecting (which would break payments).
    req = _FakeRequest({}, client_host="172.18.0.6")
    assert _verify_yookassa_source(req) is None


# ── add_device metadata parsing / amount validation ─────────────────────────


def test_parse_add_device_metadata_valid() -> None:
    from app.yookassa.webhook import _parse_add_device_metadata

    assert _parse_add_device_metadata(
        {"telegram_user_id": "123", "new_device_count": "7", "expected_amount_kopecks": "16000"}
    ) == (123, 7, 16000)


def test_parse_add_device_metadata_rejects_out_of_range_count() -> None:
    from app.yookassa.webhook import _parse_add_device_metadata

    assert _parse_add_device_metadata(
        {"telegram_user_id": "1", "new_device_count": "0", "expected_amount_kopecks": "100"}
    ) is None
    assert _parse_add_device_metadata(
        {"telegram_user_id": "1", "new_device_count": "21", "expected_amount_kopecks": "100"}
    ) is None


def test_parse_add_device_metadata_rejects_missing_or_invalid() -> None:
    from app.yookassa.webhook import _parse_add_device_metadata

    assert _parse_add_device_metadata({}) is None
    assert _parse_add_device_metadata(
        {"telegram_user_id": "1", "new_device_count": "5"}
    ) is None  # no expected amount
    assert _parse_add_device_metadata(
        {"telegram_user_id": "x", "new_device_count": "5", "expected_amount_kopecks": "100"}
    ) is None  # non-int telegram id
    assert _parse_add_device_metadata(
        {"telegram_user_id": "0", "new_device_count": "5", "expected_amount_kopecks": "100"}
    ) is None  # non-positive telegram id


def test_validate_add_device_amount_matches() -> None:
    from app.yookassa.webhook import _validate_add_device_amount

    assert _validate_add_device_amount(16000, 16000) is True
    assert _validate_add_device_amount(16000, 16001) is True  # +1 kop tolerance
    assert _validate_add_device_amount(16000, 15999) is True  # -1 kop tolerance


def test_validate_add_device_amount_rejects_mismatch() -> None:
    from app.yookassa.webhook import _validate_add_device_amount

    assert _validate_add_device_amount(16000, 15000) is False
    assert _validate_add_device_amount(16000, None) is False
    assert _validate_add_device_amount(0, 100) is False
