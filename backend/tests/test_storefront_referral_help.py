from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.application.referral_handler import ReferralInfo
from app.bot_transport.storefront_ui import (
    CB_HELP,
    FAQ_ITEMS,
    referral_program_keyboard,
    text_help,
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
    assert "Ваш доход от рефералки" in text
    assert "Доступно на балансе: <b>42.50 ₽</b>" in text
    assert "Всего заработано: <b>100.00 ₽</b>" in text
    assert "Зарабатывайте до 35% с каждой оплаты друга" in text
    assert "вывести на криптокошелёк" in text
    assert "До <b>35%</b> с оплат приглашённых друзей и до <b>5%</b> с их приглашений." in text
    assert "Приглашено друзей: <b>3</b>" in text
    assert "Подарено дней: <b>30</b>" in text
    assert "1 день —" not in text
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
    assert "+" not in telegram_button["url"]
    assert "%20" in telegram_button["url"]

    assert web_button == {
        "text": "🌐 Пригласить друга без Telegram",
        "copy_text": {"text": "https://bravada-connect.ru/?ref=ABC123"},
    }


def test_help_text_contains_current_frontend_faq_without_question_buttons() -> None:
    assert len(FAQ_ITEMS) == 10
    assert FAQ_ITEMS[0][0] == "Что такое услуга защищённого сетевого доступа?"
    assert FAQ_ITEMS[-1][0] == "Происходит ли автопродление подписки?"
    text = text_help()
    assert "Выберите вопрос" not in text
    for question, answer in FAQ_ITEMS:
        assert f"<blockquote expandable><b>{question}</b>" in text
        assert answer in text
    assert "Написать в поддержку" in text
    assert CB_HELP == "help"
