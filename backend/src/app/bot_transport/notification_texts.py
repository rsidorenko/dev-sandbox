"""Notification texts for subscription/trial lifecycle events.

Each function returns (text, inline_keyboard) for Telegram Bot API.
"""

from __future__ import annotations

from typing import Any

from app.bot_transport.storefront_ui import (
    CB_BUY_VPN,
    CB_CONNECT_DEVICE,
    CB_CONNECT_FAST_ANDROID,
    CB_CONNECT_FAST_IOS,
    CB_CONNECT_FAST_MAC,
    CB_CONNECT_FAST_WIN,
    CB_HELP,
    CB_REFERRAL,
    CB_TRIAL,
    _inline_kb,
)

_FAST_CONNECT_CALLBACKS = {
    "android": CB_CONNECT_FAST_ANDROID,
    "ios": CB_CONNECT_FAST_IOS,
    "mac": CB_CONNECT_FAST_MAC,
    "win": CB_CONNECT_FAST_WIN,
}


def _connect_callback(platform: str) -> str:
    return _FAST_CONNECT_CALLBACKS.get(platform, CB_CONNECT_DEVICE)


def text_trial_activation_nudge() -> tuple[str, dict[str, Any]]:
    text = (
        "👋 <b>Ваш бесплатный VPN готов к запуску</b>\n\n"
        "Откройте полный доступ на 3 дня — без оплаты и привязки карты. "
        "После активации останется выбрать устройство и подключиться."
    )
    keyboard = _inline_kb([[{"text": "🎁 Запустить бесплатные 3 дня", "callback_data": CB_TRIAL, "style": "success"}]])
    return text, keyboard


def text_trial_connection_nudge(*, platform: str = "") -> tuple[str, dict[str, Any]]:
    text = (
        "🔌 <b>Остался последний шаг</b>\n\n"
        "Пробный доступ уже активен, но VPN ещё не передавал трафик. "
        "Откройте подключение — настройка обычно занимает около минуты."
    )
    keyboard = _inline_kb(
        [[{"text": "📱 Завершить подключение", "callback_data": _connect_callback(platform), "style": "primary"}]]
    )
    return text, keyboard


def text_checkout_abandoned() -> tuple[str, dict[str, Any]]:
    text = (
        "💳 <b>Оплата не была завершена</b>\n\n"
        "VPN можно продолжить без повторной настройки. Вернитесь к тарифам и выберите удобный период доступа."
    )
    keyboard = _inline_kb([[{"text": "⚡ Вернуться к тарифам", "callback_data": CB_BUY_VPN, "style": "success"}]])
    return text, keyboard


def text_trial_expiring(
    *,
    active_until: str | None = None,
    has_traffic: bool = True,
    platform: str = "",
) -> tuple[str, dict[str, Any]]:
    """Совместимый alias для обычного предупреждения об окончании триала."""
    return text_trial_expiring_24h(active_until=active_until, has_traffic=has_traffic, platform=platform)


def text_trial_expiring_24h(
    *,
    active_until: str | None = None,
    has_traffic: bool = True,
    platform: str = "",
) -> tuple[str, dict[str, Any]]:
    title = (
        f"Бесплатный доступ действует до {active_until}"
        if active_until
        else "До конца бесплатного доступа меньше суток"
    )
    if has_traffic:
        text = (
            f"⏳ <b>{title}</b>\n\n"
            "Вы уже подключали Bravada VPN. Выберите тариф заранее, "
            "чтобы соединение продолжило работать без перерыва."
        )
        keyboard = _inline_kb([[{"text": "⚡ Продлить без перерыва", "callback_data": CB_BUY_VPN, "style": "success"}]])
    else:
        text = (
            f"🔌 <b>{title}</b>\n\n"
            "Похоже, VPN ещё не удалось проверить. Завершите подключение сейчас — "
            "настройка обычно занимает около минуты."
        )
        keyboard = _inline_kb(
            [[{"text": "📱 Завершить подключение", "callback_data": _connect_callback(platform), "style": "primary"}]]
        )
    return text, keyboard


def text_trial_expiring_6h(
    *,
    active_until: str | None = None,
    has_traffic: bool = True,
    platform: str = "",
) -> tuple[str, dict[str, Any]]:
    paid_title = f"VPN приостановится {active_until}" if active_until else "VPN приостановится через несколько часов"
    trial_title = (
        f"Пробный доступ закончится {active_until}"
        if active_until
        else "Пробный доступ закончится через несколько часов"
    )
    if has_traffic:
        text = (
            f"⚡ <b>{paid_title}</b>\n\n"
            "Приложение и настройки сохранятся. Выберите тариф, "
            "чтобы Bravada продолжил работать без отключения."
        )
        keyboard = _inline_kb([[{"text": "🔑 Сохранить доступ", "callback_data": CB_BUY_VPN, "style": "success"}]])
    else:
        text = f"⚡ <b>{trial_title}</b>\n\nVPN ещё не передавал трафик. У вас остаётся время проверить подключение."
        keyboard = _inline_kb(
            [[{"text": "📱 Проверить VPN", "callback_data": _connect_callback(platform), "style": "primary"}]]
        )
    return text, keyboard


def text_trial_expired() -> tuple[str, dict[str, Any]]:
    text = (
        "🔒 <b>Bravada VPN приостановлен</b>\n\n"
        "Бесплатные 3 дня закончились, но приложение и настройки сохранены. "
        "После оплаты доступ вернётся автоматически — подключать всё заново не придётся."
    )
    keyboard = _inline_kb([[{"text": "⚡ Вернуть VPN", "callback_data": CB_BUY_VPN, "style": "success"}]])
    return text, keyboard


def text_trial_expired_followup() -> tuple[str, dict[str, Any]]:
    text = (
        "🔐 <b>Ваш VPN можно вернуть в один клик</b>\n\n"
        "Подключение сохранено. Выберите тариф — доступ включится автоматически сразу после оплаты."
    )
    keyboard = _inline_kb(
        [
            [{"text": "⚡ Возобновить VPN", "callback_data": CB_BUY_VPN, "style": "success"}],
            [{"text": "❓ Нужна помощь", "callback_data": CB_HELP}],
        ]
    )
    return text, keyboard


def text_subscription_expiring(active_until: str) -> tuple[str, dict[str, Any]]:
    text = (
        "⏳ <b>Подписка Bravada скоро закончится</b>\n\n"
        f"Доступ работает до <b>{active_until}</b>. Продлите подписку заранее, "
        "чтобы VPN не приостанавливался."
    )
    keyboard = _inline_kb([[{"text": "⚡ Продлить без перерыва", "callback_data": CB_BUY_VPN, "style": "success"}]])
    return text, keyboard


def text_subscription_expired() -> tuple[str, dict[str, Any]]:
    text = (
        "🔒 <b>Подписка Bravada закончилась</b>\n\n"
        "VPN приостановлен, но подключение сохранено ещё на 20 дней. "
        "Продлите подписку — доступ восстановится автоматически."
    )
    keyboard = _inline_kb([[{"text": "⚡ Восстановить VPN", "callback_data": CB_BUY_VPN, "style": "success"}]])
    return text, keyboard


def text_keys_deleted() -> tuple[str, dict[str, Any]]:
    text = (
        "ℹ️ <b>Настройки предыдущего подключения удалены</b>\n\n"
        "После окончания подписки прошло 20 дней. При новой покупке Bravada "
        "автоматически создаст новое подключение."
    )
    keyboard = _inline_kb([[{"text": "📱 Подключить снова", "callback_data": CB_BUY_VPN, "style": "success"}]])
    return text, keyboard


def text_referral_offer_trial() -> tuple[str, dict[str, Any]]:
    """Реферальный оффер после первого реального трафика в триале (once-ever)."""
    text = (
        "🎁 <b>Хотите пользоваться Bravada бесплатно?</b>\n\n"
        "Пригласите друга: когда он оформит подписку от 1 месяца, "
        "вам добавятся <b>30 дней VPN</b>.\n\n"
        "1 друг = <b>+30 дней</b>."
    )
    keyboard = _inline_kb([[{"text": "🎁 Получить свою ссылку", "callback_data": CB_REFERRAL}]])
    return text, keyboard


def text_referral_offer_paid() -> tuple[str, dict[str, Any]]:
    """Реферальный оффер после первой оплаты для тех, кто не видел trial-оффер."""
    text = (
        "🎁 <b>Подписку можно продлевать бесплатно</b>\n\n"
        "Пригласите друга: когда он оформит подписку от 1 месяца, "
        "вам добавятся <b>30 дней VPN</b>.\n\n"
        "1 друг = <b>+30 дней</b>."
    )
    keyboard = _inline_kb([[{"text": "🎁 Получить свою ссылку", "callback_data": CB_REFERRAL}]])
    return text, keyboard
