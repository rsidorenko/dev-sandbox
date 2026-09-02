from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.application.referral_handler import ReferralInfo
from app.bot_transport.storefront_ui import (
    CB_HELP,
    CB_HELP_FAQ,
    FAQ_ITEMS,
    get_help_faq_item,
    help_faq_answer_keyboard,
    help_faq_keyboard,
    referral_program_keyboard,
    text_help,
    text_help_faq_answer,
    text_referral_program,
)


def _referral_info() -> ReferralInfo:
    return ReferralInfo(
        referral_code="ABC123",
        referral_link="https://t.me/bravada_bot?start=ref_ABC123",
        web_referral_link="https://bravada-connect.ru/?ref=ABC123",
        balance_rubles=42.5,
        l1_invited=3,
        l2_invited=7,
        total_earned_rubles=100.0,
        bonus_days_awarded=30,
    )


def test_referral_screen_keeps_free_month_offer_and_hides_rates() -> None:
    text = text_referral_program(_referral_info())

    assert "Приведи друга — получи месяц бесплатно" in text
    assert "30 дней" in text
    assert "Приглашено друзей: 3" in text
    assert "Подарено дней: 30" in text
    assert "35%" not in text
    assert "Со 2-го уровня" not in text
    assert "https://" not in text


def test_referral_keyboard_has_telegram_share_and_web_referral_link() -> None:
    keyboard = referral_program_keyboard(_referral_info())
    rows = keyboard["inline_keyboard"]
    telegram_button = rows[0][0]
    web_button = rows[1][0]

    assert telegram_button["text"] == "📨 Пригласить друга в Telegram"
    parsed_share_url = urlparse(telegram_button["url"])
    assert parsed_share_url.scheme == "https"
    assert parsed_share_url.netloc == "t.me"
    assert parsed_share_url.path == "/share/url"
    query = parse_qs(parsed_share_url.query)
    assert query["url"] == ["https://t.me/bravada_bot?start=ref_ABC123"]
    assert query["text"] == ["Попробуй Bravada VPN — безопасный доступ к интернету."]

    assert web_button == {
        "text": "🌐 Пригласить друга без Telegram",
        "url": "https://bravada-connect.ru/?ref=ABC123",
    }


def test_help_catalog_contains_current_frontend_faq_and_safe_callbacks() -> None:
    assert len(FAQ_ITEMS) == 10
    assert FAQ_ITEMS[0][0] == "Что такое услуга защищённого сетевого доступа?"
    assert FAQ_ITEMS[-1][0] == "Происходит ли автопродление подписки?"
    assert "Выберите вопрос" in text_help()

    buttons = [button for row in help_faq_keyboard()["inline_keyboard"][: len(FAQ_ITEMS)] for button in row]
    assert [button["callback_data"] for button in buttons] == [
        f"{CB_HELP_FAQ}{index}" for index in range(len(FAQ_ITEMS))
    ]
    assert all(len(button["callback_data"].encode()) <= 64 for button in buttons)


def test_help_faq_answer_and_unknown_callback() -> None:
    question, answer = FAQ_ITEMS[3]
    assert get_help_faq_item(f"{CB_HELP_FAQ}3") == (question, answer)
    assert get_help_faq_item(f"{CB_HELP_FAQ}99") is None
    assert get_help_faq_item(f"{CB_HELP_FAQ}not-a-number") is None
    assert text_help_faq_answer(question, answer) == f"<b>❓ {question}</b>\n\n{answer}"

    callback_rows = help_faq_answer_keyboard()["inline_keyboard"]
    assert callback_rows[0][0]["callback_data"] == CB_HELP
