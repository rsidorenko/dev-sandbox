"""Tests for the standard subscription metadata headers on /sub/
(profile-title / subscription-userinfo / support-url / profile-update-interval
/ announce with remaining days — see app.web_api.subscription.
_profile_meta_headers and the Happ dev docs "App management", standard
parameters)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest

from app.web_api.subscription import _DEFAULT_PROFILE_TITLE, _profile_meta_headers, _ru_days

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


def _decode_b64(value: str) -> str:
    assert value.startswith("base64:")
    return base64.b64decode(value[len("base64:") :], validate=True).decode("utf-8")


def test_title_is_default_bravada_with_emoji_base64(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(active_until_utc=_PAID), now=_NOW)
    title = _decode_b64(headers["profile-title"])
    assert title == _DEFAULT_PROFILE_TITLE == "🛡️ Bravada VPN"
    # Happ hard limit for the decoded title is 25 characters
    assert len(title) <= 25


def test_title_env_override_wins(monkeypatch) -> None:
    monkeypatch.setenv("SUB_PROFILE_TITLE", "✈️ Test VPN")
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    assert _decode_b64(_profile_meta_headers(_row(), now=_NOW)["profile-title"]) == "✈️ Test VPN"


def test_update_interval_is_12_hours(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    assert _profile_meta_headers(_row(), now=_NOW)["profile-update-interval"] == "12"


def test_expire_from_paid_subscription(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(active_until_utc=_PAID), now=_NOW)
    assert headers["subscription-userinfo"] == f"upload=0; download=0; total=0; expire={int(_PAID.timestamp())}"


def test_expire_is_later_of_trial_and_paid(monkeypatch) -> None:
    """Admin grant / renewal can leave both windows set — the later one wins
    (mirrors _entitlement_active's OR semantics)."""
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(trial_expires_at=_TRIAL, active_until_utc=_PAID), now=_NOW)
    assert headers["subscription-userinfo"].endswith(f"expire={int(_PAID.timestamp())}")

    headers = _profile_meta_headers(_row(trial_expires_at=_PAID, active_until_utc=_TRIAL), now=_NOW)
    assert headers["subscription-userinfo"].endswith(f"expire={int(_PAID.timestamp())}")


def test_expire_trial_only(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(plan_id=None, trial_expires_at=_TRIAL), now=_NOW)
    assert headers["subscription-userinfo"].endswith(f"expire={int(_TRIAL.timestamp())}")


def test_no_expiry_timestamps_omits_userinfo_and_announce(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(), now=_NOW)
    assert "subscription-userinfo" not in headers
    assert "announce" not in headers


def test_announce_days_exact_whole_days(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(active_until_utc=_NOW + timedelta(days=3)), now=_NOW)
    assert _decode_b64(headers["announce"]) == "⏳ Осталось: 3 дня"


def test_announce_days_rounds_partial_day_up(monkeypatch) -> None:
    """3 days 5 hours left → «4 дня» — the partial day still counts (the user
    has that day, same calendar feel as Happ's own day chip)."""
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(active_until_utc=_NOW + timedelta(days=3, hours=5)), now=_NOW)
    assert _decode_b64(headers["announce"]) == "⏳ Осталось: 4 дня"


def test_announce_less_than_a_day_left_is_one_day(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(active_until_utc=_NOW + timedelta(hours=5)), now=_NOW)
    assert _decode_b64(headers["announce"]) == "⏳ Осталось: 1 день"


def test_announce_uses_same_expiry_as_userinfo(monkeypatch) -> None:
    """Announce and expire must agree: both derive from the later of the two
    windows, so the day count never contradicts the bar's date."""
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(trial_expires_at=_TRIAL, active_until_utc=_PAID), now=_NOW)
    assert headers["subscription-userinfo"].endswith(f"expire={int(_PAID.timestamp())}")
    assert _decode_b64(headers["announce"]) == "⏳ Осталось: 30 дней"


def test_announce_omitted_when_expired(monkeypatch) -> None:
    """Past the window the announcement is dropped — Happ's own expired state
    (from expire in userinfo) speaks for itself."""
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(active_until_utc=_NOW - timedelta(seconds=1)), now=_NOW)
    assert "announce" not in headers
    # userinfo is still emitted — it is what flips the profile to expired
    assert headers["subscription-userinfo"].endswith(f"expire={int((_NOW - timedelta(seconds=1)).timestamp())}")


def test_announce_well_under_200_char_limit(monkeypatch) -> None:
    """Happ displays at most 200 characters of announcement text — even a
    365-day renewal must stay far below that."""
    monkeypatch.setenv("BOT_USERNAME", "bravada_vpn_bot")
    headers = _profile_meta_headers(_row(active_until_utc=_NOW + timedelta(days=365)), now=_NOW)
    assert len(_decode_b64(headers["announce"])) <= 200


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "1 день"),
        (2, "2 дня"),
        (3, "3 дня"),
        (4, "4 дня"),
        (5, "5 дней"),
        (11, "11 дней"),
        (14, "14 дней"),
        (21, "21 день"),
        (22, "22 дня"),
        (25, "25 дней"),
        (30, "30 дней"),
        (100, "100 дней"),
        (101, "101 день"),
        (111, "111 дней"),
        (365, "365 дней"),
    ],
)
def test_ru_days_plural(count: int, expected: str) -> None:
    assert _ru_days(count) == expected


def test_support_url_from_bot_username_stripping_at(monkeypatch) -> None:
    monkeypatch.setenv("BOT_USERNAME", "@BravadaVPNBot")
    assert _profile_meta_headers(_row(), now=_NOW)["support-url"] == "https://t.me/BravadaVPNBot"


def test_support_url_omitted_without_bot_username(monkeypatch) -> None:
    monkeypatch.delenv("BOT_USERNAME", raising=False)
    assert "support-url" not in _profile_meta_headers(_row(active_until_utc=_PAID), now=_NOW)
