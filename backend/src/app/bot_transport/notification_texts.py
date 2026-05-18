"""Notification texts for subscription/trial lifecycle events.

Each function returns (text, inline_keyboard) for Telegram Bot API.
"""

from __future__ import annotations

from typing import Any

from app.bot_transport.storefront_ui import (
    CB_BUY_VPN,
    CB_MAIN_MENU,
    _inline_kb,
)


def text_trial_expiring() -> tuple[str, dict[str, Any]]:
    text = (
        "⏳ Бесплатный период заканчивается!\n\n"
        "Через 24 часа доступ к VPN будет приостановлен.\n"
        "Оформите подписку, чтобы продолжить пользоваться VPN."
    )
    keyboard = _inline_kb([
        [{"text": "🔑 Купить подписку", "callback_data": CB_BUY_VPN}],
        [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
    ])
    return text, keyboard


def text_trial_expired() -> tuple[str, dict[str, Any]]:
    text = (
        "❌ Бесплатный период закончился.\n\n"
        "Доступ к VPN приостановлен.\n"
        "Ваши ключи сохранены — при оформлении подписки они активируются автоматически.\n\n"
        "У вас есть 20 дней, после чего ключи будут удалены."
    )
    keyboard = _inline_kb([
        [{"text": "🔑 Купить подписку", "callback_data": CB_BUY_VPN}],
        [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
    ])
    return text, keyboard


def text_subscription_expiring(active_until: str) -> tuple[str, dict[str, Any]]:
    text = (
        f"⏳ Подписка заканчивается!\n\n"
        f"Действует до: {active_until}\n\n"
        f"Продлите подписку, чтобы не потерять доступ к VPN."
    )
    keyboard = _inline_kb([
        [{"text": "💳 Продлить подписку", "callback_data": CB_BUY_VPN}],
        [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
    ])
    return text, keyboard


def text_subscription_expired() -> tuple[str, dict[str, Any]]:
    text = (
        "❌ Подписка истекла.\n\n"
        "Доступ к VPN приостановлен.\n"
        "Ваши ключи сохранены — при продлении они активируются автоматически.\n\n"
        "У вас есть 20 дней, после чего ключи будут удалены."
    )
    keyboard = _inline_kb([
        [{"text": "💳 Продлить подписку", "callback_data": CB_BUY_VPN}],
        [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
    ])
    return text, keyboard


def text_keys_deleted() -> tuple[str, dict[str, Any]]:
    text = (
        "🗑 Ключи удалены.\n\n"
        "Поскольку подписка не была продлена в течение 20 дней, "
        "ваши VPN-ключи были удалены.\n\n"
        "При оформлении новой подписки вам будут выданы новые ключи."
    )
    keyboard = _inline_kb([
        [{"text": "🔑 Купить подписку", "callback_data": CB_BUY_VPN}],
        [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
    ])
    return text, keyboard
