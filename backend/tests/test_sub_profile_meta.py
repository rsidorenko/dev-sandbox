"""Tests for the standard subscription metadata headers on /sub/
(profile-title / subscription-userinfo / support-url / profile-update-interval
— see app.web_api.subscription._profile_meta_headers and the Happ dev docs
"App management", standard parameters)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

from app.web_api.subscription import _DEFAULT_PROFILE_TITLE, _profile_meta_headers

_NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)
_TRIAL = _NOW + timedelta(days=3)
_PAID = _NOW + timedelta(days=30)


def _row(**overrides) -> dict:
    base = {
        "state_label": "active",
        "plan_id": "1m",
        "trial_expires_at": None,
        "active_until_utc": None,
        "keys_deleted_at": None,
    }
    base.update(overrides)
    return base


def _decode_title(value: str) -> str:
    assert value.startswith("base64:")
    return base64.b64decode(value[len("base64:") :], validate=True).decode("utf-8")


def test_title_is_default_bravada_with_emoji_base64(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(active_until_utc=_PAID))
    title = _decode_title(headers["profile-title"])
    assert title == _DEFAULT_PROFILE_TITLE == "🛡️ Bravada VPN"
    # Happ hard limit for the decoded title is 25 characters
    assert len(title) <= 25


def test_title_env_override_wins(monkeypatch) -> None:
    monkeypatch.setenv("SUB_PROFILE_TITLE", "✈️ Test VPN")
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    assert _decode_title(_profile_meta_headers(_row())["profile-title"]) == "✈️ Test VPN"


def test_update_interval_is_24_hours(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    assert _profile_meta_headers(_row())["profile-update-interval"] == "24"


def test_expire_from_paid_subscription(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(active_until_utc=_PAID))
    assert headers["subscription-userinfo"] == f"upload=0; download=0; total=0; expire={int(_PAID.timestamp())}"


def test_expire_is_later_of_trial_and_paid(monkeypatch) -> None:
    """Admin grant / renewal can leave both windows set — the later one wins
    (mirrors _entitlement_active's OR semantics)."""
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(trial_expires_at=_TRIAL, active_until_utc=_PAID))
    assert headers["subscription-userinfo"].endswith(f"expire={int(_PAID.timestamp())}")

    headers = _profile_meta_headers(_row(trial_expires_at=_PAID, active_until_utc=_TRIAL))
    assert headers["subscription-userinfo"].endswith(f"expire={int(_PAID.timestamp())}")


def test_expire_trial_only(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(plan_id=None, trial_expires_at=_TRIAL))
    assert headers["subscription-userinfo"].endswith(f"expire={int(_TRIAL.timestamp())}")


def test_no_expiry_timestamps_omits_userinfo(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row())
    assert "subscription-userinfo" not in headers


def test_support_url_from_bot_username_stripping_at(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "@BravadaVPNBot")
    assert _profile_meta_headers(_row())["support-url"] == "https://t.me/BravadaVPNBot"


def test_support_url_omitted_without_bot_username(monkeypatch) -> None:
    monkeypatch.delenv("BOT_USERNAME", raising=False)
    assert "support-url" not in _profile_meta_headers(_row(active_until_utc=_PAID))
