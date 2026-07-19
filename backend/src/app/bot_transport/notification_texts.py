"""Notification texts for subscription/trial lifecycle events.

Each function returns (text, inline_keyboard) for Telegram Bot API.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.bot_transport.storefront_ui import (
    CB_BUY_VPN,
    CB_MAIN_MENU,
    CB_REFERRAL,
    _inline_kb,
)


def text_trial_expiring(
    *,
    promo_code: str | None = None,
    discount_percent: int = 0,
    checkout_url: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """'Trial ends tomorrow' notice. Doubles as an urgency referral trigger, and when
    promo_code/checkout_url are supplied (trial-eve scheduler path) carries a personal
    discount CTA that deep-links into the web checkout with the promo pre-filled."""
    text = (
        "⏳ Бесплатный период заканчивается!\n\n"
        "Через 24 часа доступ к VPN будет приостановлен.\n"
        "Оформите подписку, чтобы продолжить пользоваться VPN."
    )
    rows: list[list[dict[str, str]]] = []
    if promo_code and checkout_url:
        text += (
            f"\n\n🎉 Скидка {discount_percent}% по промокоду `{promo_code}` — оформите сегодня, код действует 7 дней."
        )
        rows.append([{"text": f"🛒 Оформить со скидкой {discount_percent}%", "url": checkout_url}])
    text += "\n\n👥 А пока позовите друга своей ссылкой — получите до 35% с его оплаты."
    rows.append([{"text": "🔑 Купить подписку", "callback_data": CB_BUY_VPN}])
    rows.append([{"text": "👥 Реферальная программа", "callback_data": CB_REFERRAL}])
    rows.append([{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}])
    return text, _inline_kb(rows)


def text_referral_cta_after_payment(*, tg_link: str) -> tuple[str, dict[str, Any]]:
    """Реферальный призыв сразу после успешной оплаты (отправляется отдельным
    best-effort сообщением через fulfillment-нотификатор, parse_mode=None → plain text).
    Кнопка «Поделиться» открывает диалог шеринга в Telegram с готовым текстом."""
    share_text = quote(
        "Защищённый VPN на Reality-протоколе — работает там, где другие сломались. 3 дня бесплатно по моей ссылке:"
    )
    share_url = f"https://t.me/share/url?url={quote(tg_link, safe='')}&text={share_text}"
    keyboard = _inline_kb(
        [
            [{"text": "📤 Поделиться ссылкой", "url": share_url}],
            [{"text": "👥 Реферальная программа", "callback_data": CB_REFERRAL}],
            [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
        ]
    )
    text = (
        "🎁 Спасибо за оплату — подписка активна!\n\n"
        "Позовите друга своей ссылкой и получите до 35% с каждой его оплаты. "
        "Заработанное можно тратить на продление подписки.\n\n"
        f"🔗 Ваша ссылка:\n{tg_link}"
    )
    return text, keyboard


def text_trial_expired() -> tuple[str, dict[str, Any]]:
    text = (
        "❌ Бесплатный период закончился.\n\n"
        "Доступ к VPN приостановлен.\n"
        "Ваши ключи сохранены — при оформлении подписки они активируются автоматически.\n\n"
        "У вас есть 20 дней, после чего ключи будут удалены."
    )
    keyboard = _inline_kb(
        [
            [{"text": "🔑 Купить подписку", "callback_data": CB_BUY_VPN}],
            [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
        ]
    )
    return text, keyboard


def text_subscription_expiring(active_until: str) -> tuple[str, dict[str, Any]]:
    text = (
        f"⏳ Подписка заканчивается!\n\n"
        f"Действует до: {active_until}\n\n"
        f"Продлите подписку, чтобы не потерять доступ к VPN."
    )
    keyboard = _inline_kb(
        [
            [{"text": "💳 Продлить подписку", "callback_data": CB_BUY_VPN}],
            [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
        ]
    )
    return text, keyboard


def text_subscription_expired() -> tuple[str, dict[str, Any]]:
    text = (
        "❌ Подписка истекла.\n\n"
        "Доступ к VPN приостановлен.\n"
        "Ваши ключи сохранены — при продлении они активируются автоматически.\n\n"
        "У вас есть 20 дней, после чего ключи будут удалены."
    )
    keyboard = _inline_kb(
        [
            [{"text": "💳 Продлить подписку", "callback_data": CB_BUY_VPN}],
            [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
        ]
    )
    return text, keyboard


def text_keys_deleted() -> tuple[str, dict[str, Any]]:
    text = (
        "🗑 Ключи удалены.\n\n"
        "Поскольку подписка не была продлена в течение 20 дней, "
        "ваши VPN-ключи были удалены.\n\n"
        "При оформлении новой подписки вам будут выданы новые ключи."
    )
    keyboard = _inline_kb(
        [
            [{"text": "🔑 Купить подписку", "callback_data": CB_BUY_VPN}],
            [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
        ]
    )
    return text, keyboard
