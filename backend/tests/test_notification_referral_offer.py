"""Tests for the referral bot-offer (C2): notification texts and the feature-flag helper."""

from app.bot_transport.notification_texts import (
    text_referral_offer_paid,
    text_referral_offer_trial,
)
from app.bot_transport.storefront_ui import CB_REFERRAL
from app.runtime.notification_scheduler import _referral_offer_enabled


def _callback_data(keyboard: dict) -> list[str]:
    return [btn["callback_data"] for row in keyboard["inline_keyboard"] for btn in row]


def test_referral_offer_trial_text_and_keyboard() -> None:
    text, keyboard = text_referral_offer_trial()
    assert "+30 дней" in text
    assert "бесплатный месяц" in text
    assert CB_REFERRAL in _callback_data(keyboard)


def test_referral_offer_paid_text_and_keyboard() -> None:
    text, keyboard = text_referral_offer_paid()
    assert "+30 дней" in text
    assert "бесплатный месяц" in text
    assert CB_REFERRAL in _callback_data(keyboard)


def test_trial_and_paid_texts_are_distinct() -> None:
    """Two different pushes (trial vs paid) — not 'the same push' twice."""
    trial_text, _ = text_referral_offer_trial()
    paid_text, _ = text_referral_offer_paid()
    assert trial_text != paid_text


def test_referral_offer_flag_default_on() -> None:
    assert _referral_offer_enabled(lambda _key: None) is True  # missing env → ON
    assert _referral_offer_enabled(lambda _key: "") is True


def test_referral_offer_flag_disable_values() -> None:
    for raw in ("0", "false", "no", "off", "FALSE", " No "):
        assert _referral_offer_enabled(lambda _key, _v=raw: _v) is False
