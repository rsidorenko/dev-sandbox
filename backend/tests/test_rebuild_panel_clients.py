"""Unit tests for rebuild_panel_clients operator script (no DB/network).

Loads the script via importlib (same pattern as test_run_slice1_retention_dry_run)
and covers the pure helpers — in particular the regression where ``main()`` /
``--verify`` crashed with ``TypeError: 'NoneType' object is not subscriptable``
because the user-summary line subscripted ``u['vless_uuid']`` which has been NULL
for every Telegram user since PR #320.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _BACKEND_DIR / "scripts" / "rebuild_panel_clients.py"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rebuild_panel_clients", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load_script_module()


# ---------------------------------------------------------------------------
# Regression: the user-summary line must not touch vless_uuid (NULL since PR #320)
# ---------------------------------------------------------------------------


def test_user_summary_line_formats_post_pr320_user_dict(script: ModuleType) -> None:
    """A user record carrying no vless_uuid key (the post-PR#320 shape produced by
    _load_active_users) must format without raising."""
    user = {"internal_user_id": "u662796891", "device_count": 5, "active_until_utc": datetime(2026, 8, 4, tzinfo=UTC)}
    line = script._user_summary_line(user)
    assert "user=u662796891" in line
    assert "devices=5" in line
    assert "2026-08-04" in line
    # internal_user_id is truncated to 16 chars (matches _email_from_internal width).
    long_user = {
        "internal_user_id": "u" + "x" * 30,
        "device_count": 1,
        "active_until_utc": datetime(2026, 8, 4, tzinfo=UTC),
    }
    long_line = script._user_summary_line(long_user)
    assert "user=u" + "x" * 15 + "  " in long_line  # exactly 16 chars after "user="


def test_user_summary_line_survives_vless_uuid_none(script: ModuleType) -> None:
    """The exact pre-fix crash: a dict with vless_uuid=None must NOT raise when
    summarised. (Previously the line did ``u['vless_uuid'][:8]`` -> TypeError.)
    The helper must also be independent of the key — output identical with or
    without it, so the dead column can never be reintroduced as a dependency."""
    base = {"internal_user_id": "uabc", "device_count": 3, "active_until_utc": datetime(2026, 8, 4, tzinfo=UTC)}
    with_none = {**base, "vless_uuid": None}
    with_val = {**base, "vless_uuid": "12345678-1234-1234-1234-123456789012"}

    line_none = script._user_summary_line(with_none)
    line_val = script._user_summary_line(with_val)
    line_base = script._user_summary_line(base)

    # No crash, no leak of the dead column into the output.
    assert line_none == line_val == line_base
    assert "vless_uuid" not in line_none
    assert "None" not in line_none


# ---------------------------------------------------------------------------
# Mirror contract: helpers must match app.issuance.xui_vless_provider
# ---------------------------------------------------------------------------


def test_vless_uuid_for_transport_is_deterministic_and_unique(script: ModuleType) -> None:
    """Per-(user,transport) derived uuid: stable, distinct per transport, distinct
    per user (uuid5 mirror of xui_vless_provider._vless_uuid_for_transport)."""
    tcp_a = script._vless_uuid_for_transport("u111", "tcp")
    tcp_a_again = script._vless_uuid_for_transport("u111", "tcp")
    xhttp_a = script._vless_uuid_for_transport("u111", "xhttp")
    tcp_b = script._vless_uuid_for_transport("u222", "tcp")

    assert tcp_a == tcp_a_again  # deterministic
    assert tcp_a != xhttp_a  # differs by transport (v3 client_inbounds fix)
    assert tcp_a != tcp_b  # differs by user


def test_email_from_internal_prefix_and_truncation(script: ModuleType) -> None:
    """email = '{x-}?user-{internal_user_id[:16]}' — mirror of xui_vless_provider."""
    long_id = "u" + "x" * 30
    trunc = long_id[:16]
    assert len(trunc) == 16
    assert script._email_from_internal(long_id, transport_type="tcp") == f"user-{trunc}"
    assert script._email_from_internal(long_id, transport_type="xhttp") == f"x-user-{trunc}"
    # tcp is the default transport
    assert script._email_from_internal("uabc") == "user-uabc"


def test_is_bot_email_recognises_prefixes(script: ModuleType) -> None:
    """All bot-managed email prefixes are recognised; service/relay clients are not."""
    assert script._is_bot_email("user-u123") is True
    assert script._is_bot_email("x-user-u123") is True
    assert script._is_bot_email("cdn-user-u123") is True  # legacy CDN, kept for cleanup
    assert script._is_bot_email("relay-from-ru-relay") is False
    assert script._is_bot_email("") is False


def test_expiry_from_datetime_future_and_fallback(script: ModuleType) -> None:
    """Explicit future datetime is used verbatim (ms); past/None falls back to ~365d."""
    future = datetime.now(UTC) + timedelta(days=30)
    ts = script._expiry_from_datetime(future)
    assert abs(ts - int(future.timestamp() * 1000)) < 1000  # within 1s

    fallback_none = script._expiry_from_datetime(None)
    fallback_past = script._expiry_from_datetime(datetime(2020, 1, 1, tzinfo=UTC))
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    # Both fallbacks land roughly 365 days out (364..366 to avoid wall-clock edges).
    for fb in (fallback_none, fallback_past):
        days_out = (fb - now_ms) / 86_400_000
        assert 363 <= days_out <= 367
