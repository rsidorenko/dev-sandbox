"""Tests for bot_transport.notification_texts: lifecycle message keyboards/texts."""

from __future__ import annotations

from app.bot_transport.notification_texts import (
    text_referral_cta_after_payment,
    text_trial_expiring,
)


def _buttons(keyboard: dict) -> list[dict]:
    return [b for row in keyboard["inline_keyboard"] for b in row]


def test_text_trial_expiring_has_referral_and_buy_buttons() -> None:
    """The 'trial ends tomorrow' message doubles as an urgency referral trigger:
    it must carry both a 'buy' and a 'referral program' button."""
    text, keyboard = text_trial_expiring()
    assert "Бесплатный период заканчивается" in text
    assert "позовите друга" in text.lower()
    buttons = _buttons(keyboard)
    assert any(b.get("callback_data") == "buy_vpn" for b in buttons)
    assert any(b.get("callback_data") == "referral_program" for b in buttons)


def test_text_trial_expiring_with_promo_has_discount_cta_and_checkout_url() -> None:
    """When the scheduler supplies a personal promo (trial-eve path), the message carries
    the code, a discount line, and a checkout URL button that deep-links with ?promo=."""
    text, keyboard = text_trial_expiring(
        promo_code="BRAVADA-ABC12345",
        discount_percent=15,
        checkout_url="https://site.example/?promo=BRAVADA-ABC12345",
    )
    assert "BRAVADA-ABC12345" in text
    assert "15%" in text
    assert "позовите друга" in text.lower()  # referral CTA still present
    buttons = _buttons(keyboard)
    checkout = next(b for b in buttons if b.get("url"))
    assert "promo=BRAVADA-ABC12345" in checkout["url"]
    assert any(b.get("callback_data") == "buy_vpn" for b in buttons)


def test_text_referral_cta_after_payment_has_share_and_link() -> None:
    text, keyboard = text_referral_cta_after_payment(tg_link="https://t.me/bot?start=ref_ABC12345")
    # Plain-text link is included (auto-linked by Telegram under parse_mode=None).
    assert "https://t.me/bot?start=ref_ABC12345" in text
    assert "Позовите друга" in text
    buttons = _buttons(keyboard)
    # Share URL button opens the Telegram share dialog with the referral link + message.
    share_url = next(b["url"] for b in buttons if b.get("url", "").startswith("https://t.me/share/url"))
    assert "ref_ABC12345" in share_url
    # Plus a referral-program menu button.
    assert any(b.get("callback_data") == "referral_program" for b in buttons)
