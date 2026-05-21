"""Новый UI Telegram-бота: inline-клавиатуры, тексты на русском, callback-навигация.

Создан как отдельный слой поверх существующего message_catalog/outbound.
Параллельно работает со старым UI, не ломает существующие тесты.
"""

from __future__ import annotations

from typing import Any

from app.application.purchase_handler import PurchasePlanOption, PurchaseSummary
from app.application.referral_handler import ReferralBalanceInfo, ReferralInfo
from app.domain.devices import DEFAULT_DEVICE_LIMIT, EXTRA_DEVICE_PRICE_RUBLES, MAX_DEVICE_COUNT
from app.domain.plans import CUSTOM_DAY_PRICE_RUBLES, plan_display_name
from app.issuance.vless_provider import VlessServerConfig, VlessUserConfig, format_key_list

# ─── Callback data constants ──────────────────────────────────────────

CB_MAIN_MENU = "main_menu"
CB_BUY_VPN = "buy_vpn"
CB_MY_SUB = "my_subscription"
CB_MY_KEYS = "my_keys"
CB_SUB_URL = "subscription_url"
CB_REFERRAL = "referral_program"
CB_BALANCE = "balance"
CB_SETTINGS = "subscription_settings"
CB_HELP = "help"
CB_BACK = "back"
CB_PLAN = "plan:"
CB_DEVICES = "devices:"
CB_CONFIRM_PAY = "confirm_pay:"
CB_PAY_BALANCE = "pay_balance:"
CB_ROUTER = "router_config"
CB_DO_PAY = "do_pay:"
CB_ADD_DEVICE = "add_device"
CB_ADD_DEV = "add_dev:"
CB_ADD_DEV_BALANCE = "add_dev_bal:"
CB_REMOVE_DEVICE = "remove_device"
CB_LINK_EMAIL = "link_email"
CB_LINK_EMAIL_CONFIRM = "link_email_confirm:"
CB_RESEND_EMAIL_CODE = "resend_email_code"
CB_REISSUE_KEYS = "reissue_keys"
CB_REISSUE_CONFIRM = "reissue_keys_confirm"
CB_CONNECT_DEVICE = "connect_device"
CB_CONNECT_WIN = "connect_win"
CB_CONNECT_ANDROID = "connect_android"
CB_CONNECT_IOS = "connect_ios"
CB_CONNECT_MAC = "connect_mac"
CB_CONNECT_NEXT = "connect_next"
CB_CONNECT_DONE = "connect_done"
CB_TRIAL = "start_trial"
CB_ALL_KEYS = "all_keys"
CB_SERVER = "server:"
CB_CUSTOM_DAYS = "custom_days"


# ─── Inline keyboards ──────────────────────────────────────────────────

_MAX_CALLBACK_BYTES = 64


def _cb(prefix: str, value: str) -> str:
    """Build callback_data with Telegram 64-byte limit enforcement."""
    payload = f"{prefix}{value}"
    encoded = payload.encode("utf-8")
    if len(encoded) > _MAX_CALLBACK_BYTES:
        return payload[: _MAX_CALLBACK_BYTES - 3] + "..."
    return payload


def _inline_kb(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": rows}


def main_menu_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "🔑 Купить VPN", "callback_data": CB_BUY_VPN}],
            [
                {"text": "📋 Моя подписка", "callback_data": CB_MY_SUB},
                {"text": "🔐 Мои ключи", "callback_data": CB_MY_KEYS},
            ],
            [{"text": "📱 Подключить устройство", "callback_data": CB_CONNECT_DEVICE}],
            [
                {"text": "👥 Реферальная программа", "callback_data": CB_REFERRAL},
                {"text": "💰 Баланс", "callback_data": CB_BALANCE},
            ],
            [{"text": "📧 Привязать email", "callback_data": CB_LINK_EMAIL}],
            [
                {"text": "⚙️ Настройки подписки", "callback_data": CB_SETTINGS},
                {"text": "❓ Помощь", "callback_data": CB_HELP},
            ],
        ]
    )


_PLAN_EMOJI: dict[str, str] = {
    "1d": "⚪",
    "7d": "🟡",
    "14d": "🟠",
    "1m": "🟢",
    "3m": "🔵",
    "6m": "🟣",
    "365d": "🏆",
}


def buy_vpn_keyboard(plans: tuple[PurchasePlanOption, ...]) -> dict[str, Any]:
    rows = [
        [
            {
                "text": f"{_PLAN_EMOJI.get(p.plan_id, '📦')} {p.display_name} — {p.price_rubles} ₽",
                "callback_data": f"{CB_PLAN}{p.plan_id}",
            }
        ]
        for p in plans
    ]
    rows.append([{"text": "📦 Свой тариф", "callback_data": CB_CUSTOM_DAYS}])
    rows.append([{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}])
    return _inline_kb(rows)


def device_select_keyboard(plan_id: str, current: int = DEFAULT_DEVICE_LIMIT) -> dict[str, Any]:
    rows = [
        [
            {"text": "➖", "callback_data": f"{CB_DEVICES}{plan_id}:{max(1, current - 1)}"},
            {"text": f"Устройств: {current}", "callback_data": "noop"},
            {"text": "➕", "callback_data": f"{CB_DEVICES}{plan_id}:{min(MAX_DEVICE_COUNT, current + 1)}"},
        ],
        [{"text": f"✅ Продолжить ({current} устройств)", "callback_data": f"{CB_CONFIRM_PAY}{plan_id}:{current}"}],
        [{"text": "↩️ Назад к тарифам", "callback_data": CB_BUY_VPN}],
    ]
    return _inline_kb(rows)


def confirm_pay_keyboard(
    plan_id: str,
    device_count: int,
    *,
    balance_kopecks: int = 0,
    total_kopecks: int = 0,
) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = [
        [{"text": "💳 Оплатить", "callback_data": f"{CB_DO_PAY}{plan_id}:{device_count}"}],
    ]
    if balance_kopecks >= total_kopecks and total_kopecks > 0:
        rows.append(
            [
                {
                    "text": f"💰 С баланса ({balance_kopecks // 100} ₽)",
                    "callback_data": f"{CB_PAY_BALANCE}{plan_id}:{device_count}",
                }
            ]
        )
    rows.append([{"text": "↩️ Назад", "callback_data": f"{CB_DEVICES}{plan_id}:{device_count}"}])
    return _inline_kb(rows)


def back_only_keyboard(callback: str = CB_MAIN_MENU) -> dict[str, Any]:
    return _inline_kb([[{"text": "↩️ Назад", "callback_data": callback}]])


def no_subscription_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "🔑 Купить VPN", "callback_data": CB_BUY_VPN}],
            [{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}],
        ]
    )


def settings_keyboard(has_subscription: bool, device_count: int | None = None) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    if has_subscription:
        rows.append(
            [{"text": f"➕ Добавить устройство (+{EXTRA_DEVICE_PRICE_RUBLES} ₽)", "callback_data": CB_ADD_DEVICE}]
        )
        if device_count is not None and device_count > DEFAULT_DEVICE_LIMIT:
            rows.append([{"text": "➖ Убрать устройство (до 5)", "callback_data": CB_REMOVE_DEVICE}])
    rows.append([{"text": "📶 Конфиг для роутера (Скоро)", "callback_data": CB_ROUTER}])
    rows.append([{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}])
    return _inline_kb(rows)


# ─── Message texts ──────────────────────────────────────────────────────


def text_welcome(*, trial_available: bool = False) -> str:
    base = "👋 Добро пожаловать в VPN-сервис!\n\nВыберите действие в меню ниже."
    if trial_available:
        base = "👋 Добро пожаловать в VPN-сервис!\n\n🎁 Вам доступен бесплатный пробный период — 3 дня!"
    return base


def welcome_keyboard(*, trial_available: bool = False) -> dict[str, Any]:
    if trial_available:
        return _inline_kb(
            [
                [{"text": "🎁 Попробовать 3 дня бесплатно", "callback_data": CB_TRIAL}],
                [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
            ]
        )
    return main_menu_keyboard()


def text_main_menu() -> str:
    return "🏠 Главное меню"


def text_buy_vpn_intro() -> str:
    return (
        "🔑 Выберите тариф VPN-подписки:\n\n"
        "⚪ — 1 день\n"
        "🟡 — 7 дней\n"
        "🟠 — 2 недели\n"
        "🟢 — 1 месяц\n"
        "🔵 — 3 месяца\n"
        "🟣 — 6 месяцев\n"
        "🏆 — 1 год\n\n"
        "Или выберите «📦 Свой тариф» для произвольного количества дней."
    )


def text_device_select(plan_id: str, price_rubles: int, duration_days: int, device_count: int) -> str:
    from app.domain.devices import extra_device_cost, extra_device_count

    extra = extra_device_count(device_count)
    extra_cost = extra_device_cost(device_count, duration_days=duration_days)
    total = price_rubles + extra_cost
    lines = [
        f"📦 Тариф: {plan_display_name(plan_id)}",
        f"💰 Базовая цена: {price_rubles} ₽",
        f"📱 Устройств: {device_count}",
    ]
    if extra > 0:
        daily_price = EXTRA_DEVICE_PRICE_RUBLES / 30
        lines.append(f"  ➕ Доп. устройств: {extra} × {daily_price:.1f} ₽/день × {duration_days} дн = {extra_cost} ₽")
    lines.append(f"\n💳 Итого: {total} ₽")
    lines.append("\nВыберите количество устройств:")
    return "\n".join(lines)


def text_purchase_summary(summary: PurchaseSummary) -> str:
    lines = [
        "📋 Заказ:",
        "",
        f"  Тариф: {summary.plan_display_name}",
        f"  Устройств: {summary.device_count}",
    ]
    if summary.extra_devices > 0:
        daily_price = EXTRA_DEVICE_PRICE_RUBLES / 30
        lines.append(
            f"  Доп. устройств: {summary.extra_devices} × {daily_price:.1f} ₽/день × {summary.duration_days} дн = {summary.extra_device_cost_rubles} ₽"
        )
    lines.extend(["", f"💳 К оплате: {summary.total_price_rubles} ₽", "", "Нажмите «Оплатить» для перехода к оплате."])
    return "\n".join(lines)


def text_payment_unavailable() -> str:
    return "⚠️ Оплата временно недоступна. Попробуйте позже или обратитесь в поддержку."


def text_subscription_active(
    active_until: str | None,
    plan_name: str | None,
    device_count: int | None,
    *,
    remaining_days: int | None = None,
) -> str:
    lines = ["✅ Ваша подписка активна!"]
    if remaining_days is not None:
        lines.append(f"⏳ Осталось: {remaining_days} дн.")
    if device_count is not None:
        lines.append(f"📱 Устройств: {device_count}")
    if active_until:
        lines.append(f"📅 Действует до: {active_until}")
    lines.extend(["", "Используйте кнопки меню для управления 📋"])
    return "\n".join(lines)


def text_subscription_expired() -> str:
    return "❌ Ваша подписка истекла.\n\nДля продления нажмите «🔑 Купить VPN»."


def text_no_subscription() -> str:
    return "У вас нет активной подписки.\n\nНажмите «🔑 Купить VPN» для оформления."


def text_my_keys(config: VlessUserConfig) -> str:
    lines = [
        "🔐 Ваши настройки подключения:\n",
        "📎 Ссылка для подписки (нажмите, чтобы скопировать):",
        f"`{config.subscription_url}`\n",
        "🔑 Ключи:",
    ]
    lines.append(format_key_list(config.servers))
    lines.extend(["", "💡 Нажмите на ссылку или ключ, чтобы скопировать."])
    return "\n".join(lines)


def keys_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "📱 Подключить устройство", "callback_data": CB_CONNECT_DEVICE}],
            [{"text": "🔄 Перевыпустить ключи", "callback_data": CB_REISSUE_KEYS}],
            [{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}],
        ]
    )


def my_keys_menu_keyboard(servers: tuple[VlessServerConfig, ...]) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = [
        [{"text": "📋 Все ключи списком", "callback_data": CB_ALL_KEYS}],
    ]
    server_row: list[dict[str, str]] = []
    for s in servers:
        server_row.append(
            {"text": f"{s.country_flag} {s.server_label}", "callback_data": _cb(CB_SERVER, s.server_label)}
        )
        if len(server_row) == 2:
            rows.append(server_row)
            server_row = []
    if server_row:
        rows.append(server_row)
    rows.append([{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}])
    return _inline_kb(rows)


def text_my_keys_menu(*, subscription_url: str | None = None) -> str:
    lines = ["🔐 Ваши ключи\n"]
    if subscription_url:
        lines.append(f"🔗 Ссылка для подписки:\n`{subscription_url}`\n")
    lines.append("Выберите сервер или нажмите «Все ключи списком»:")
    return "\n".join(lines)


def text_single_server_key(server: VlessServerConfig) -> str:
    return f"🔑 {server.country_flag} {server.server_label}\n\n`{server.vless_link}`\n\n💡 Нажмите на ключ, чтобы скопировать."


def single_server_key_keyboard() -> dict[str, Any]:
    return _inline_kb([[{"text": "↩️ Назад", "callback_data": CB_MY_KEYS}]])


def text_all_keys_list(config: VlessUserConfig) -> str:
    lines = ["🔐 Все ваши ключи:\n"]
    lines.append(format_key_list(config.servers))
    lines.append("💡 Нажмите на ключ, чтобы скопировать.")
    return "\n".join(lines)


def all_keys_list_keyboard() -> dict[str, Any]:
    return _inline_kb([[{"text": "↩️ Назад", "callback_data": CB_MY_KEYS}]])


def text_reissue_confirm() -> str:
    return (
        "⚠️ Перевыпуск ключей\n\n"
        "Старые ключи перестанут работать.\n"
        "Все устройства нужно будет переподключить.\n\n"
        "Продолжить?"
    )


def reissue_confirm_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "✅ Да, перевыпустить", "callback_data": CB_REISSUE_CONFIRM}],
            [{"text": "↩️ Назад", "callback_data": CB_MY_KEYS}],
        ]
    )


# ─── Connect device flow ──────────────────────────────────────────

_PLATFORM_TEXTS: dict[str, str] = {
    "win": (
        "🖥 *Windows*\n\n"
        "Скачайте приложение:\n\n"
        "🔹 Karing\n"
        "https://github.com/KaringX/karing/releases\n\n"
        "🔹 Happ\n"
        "https://github.com/Happ-proxy/happ-desktop/releases\n\n"
        "Скачайте, установите и нажмите «Далее»."
    ),
    "android": (
        "📱 *Android*\n\n"
        "Скачайте приложение:\n\n"
        "🔹 Karing\n"
        "https://github.com/KaringX/karing/releases\n\n"
        "🔹 Happ\n"
        "https://play.google.com/store/apps/details?id=com.happproxy\n\n"
        "Скачайте, установите и нажмите «Далее»."
    ),
    "ios": (
        "📱 *iOS*\n\n"
        "Скачайте приложение:\n\n"
        "🔹 Karing\n"
        "https://apps.apple.com/app/karing/id6472431552\n\n"
        "🔹 Happ\n"
        "https://apps.apple.com/app/happ-proxy-utility/id6504287215\n\n"
        "Скачайте, установите и нажмите «Далее»."
    ),
    "mac": (
        "💻 *macOS*\n\n"
        "Скачайте приложение:\n\n"
        "🔹 Karing\n"
        "https://apps.apple.com/app/karing/id6472431552\n\n"
        "🔹 Happ\n"
        "https://apps.apple.com/app/happ-proxy-utility/id6504287215\n\n"
        "Скачайте, установите и нажмите «Далее»."
    ),
}

_PLATFORM_CB: dict[str, str] = {
    "win": CB_CONNECT_WIN,
    "android": CB_CONNECT_ANDROID,
    "ios": CB_CONNECT_IOS,
    "mac": CB_CONNECT_MAC,
}


def text_connect_device() -> str:
    return "📱 Выберите ваше устройство:"


def connect_device_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "🖥 Windows", "callback_data": CB_CONNECT_WIN},
                {"text": "🤖 Android", "callback_data": CB_CONNECT_ANDROID},
            ],
            [
                {"text": "🍎 iOS", "callback_data": CB_CONNECT_IOS},
                {"text": "💻 macOS", "callback_data": CB_CONNECT_MAC},
            ],
            [{"text": "↩️ Назад", "callback_data": CB_MY_KEYS}],
        ]
    )


def _platform_from_cb(cb: str) -> str | None:
    for key, val in _PLATFORM_CB.items():
        if cb == val:
            return key
    return None


def text_connect_platform(cb: str) -> str:
    platform = _platform_from_cb(cb)
    return _PLATFORM_TEXTS.get(platform, "Выберите устройство.")


def connect_platform_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "➡️ Далее", "callback_data": CB_CONNECT_NEXT}],
            [{"text": "↩️ Назад", "callback_data": CB_CONNECT_DEVICE}],
        ]
    )


def text_connect_config(config: VlessUserConfig) -> str:
    return (
        "⚙️ Подключение\n\n"
        "Скопируйте ссылку ниже:\n"
        f"`{config.subscription_url}`\n\n"
        "Откройте приложение и:\n"
        "1. Найдите раздел «Подписка» или «Subscription»\n"
        "2. Вставьте скопированную ссылку\n"
        "3. Нажмите «Импорт» / «Добавить»\n"
        "4. Подключитесь — выберите любой сервер\n\n"
        "Все настройки подтянутся автоматически!"
    )


def connect_config_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "✅ Готово", "callback_data": CB_CONNECT_DONE}],
            [{"text": "↩️ Назад", "callback_data": CB_CONNECT_DEVICE}],
        ]
    )


def text_connect_done() -> str:
    return (
        "🎉 Вы подключены!\n\nНастройки защищённого соединения активны.\nЕсли возникнут вопросы — напишите в поддержку."
    )


def connect_done_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "↩️ В главное меню", "callback_data": CB_MAIN_MENU}],
        ]
    )


def text_subscription_url(config: VlessUserConfig) -> str:
    return (
        "📎 Ссылка для приложений:\n\n"
        f"`{config.subscription_url}`\n\n"
        "💡 Скопируйте эту ссылку и вставьте в приложение:\n"
        "Karing, v2rayTune, Happ, v2rayNG и др.\n"
        "Все ключи подтянутся автоматически."
    )


def text_keys_not_available() -> str:
    return "⚠️ Ключи недоступны.\n\nВозможно, у вас нет активной подписки.\nНажмите «🔑 Купить VPN» для оформления."


def text_referral_program(info: ReferralInfo) -> str:
    return (
        "👥 Реферальная программа\n\n"
        f"🔗 Ваша ссылка:\n`{info.referral_link}`\n\n"
        f"💰 Баланс: {info.balance_rubles:.2f} ₽\n"
        f"👤 Прямых рефералов: {info.direct_referrals_count}\n\n"
        "📤 Отправляйте ссылку друзьям и получайте:\n"
        "  1 день — 10% | 7 дней — 15% | 2 недели — 20%\n"
        "  1 мес — 35% | 3 мес — 30% | 6 мес — 25% | 1 год — 25%\n"
        "Со 2-го уровня:\n"
        "  1 день — 1% | 7 дней — 2% | 2 недели — 3%\n"
        "  1 мес — 5% | 3 мес — 3% | 6 мес — 2% | 1 год — 2%\n\n"
        "💰 Реферальными деньгами можно оплачивать подписку."
    )


def text_balance(info: ReferralBalanceInfo) -> str:
    return f"💰 Ваш баланс: {info.balance_rubles:.2f} ₽"


def balance_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "🔑 Купить VPN", "callback_data": CB_BUY_VPN}],
            [{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}],
        ]
    )


def text_settings(
    has_subscription: bool,
    *,
    plan_name: str | None = None,
    device_count: int | None = None,
    active_until: str | None = None,
) -> str:
    lines = ["⚙️ Настройки подписки\n"]
    if has_subscription:
        if plan_name:
            lines.append(f"📦 Тариф: {plan_name}")
        if device_count is not None:
            lines.append(f"📱 Устройств: {device_count}")
        if active_until:
            lines.append(f"📅 Действует до: {active_until}")
        lines.append("")
        lines.append("Здесь можно изменить параметры подписки.")
    else:
        lines.append("У вас нет активной подписки.")
    return "\n".join(lines)


def text_router_soon() -> str:
    return "📶 Конфиг для роутера\n\n🔜 Эта функция скоро будет доступна!\nСледите за обновлениями."


def text_help() -> str:
    return (
        "❓ Помощь\n\n"
        "🔑 Купить VPN — выбор тарифа и оплата\n"
        "📋 Моя подписка — статус и срок действия\n"
        "🔐 Мои ключи — список VLESS-ключей\n"
        "📎 Ссылка для приложений — для Karing, Happ, v2rayTune\n"
        "👥 Реферальная программа — приглашайте друзей\n"
        "💰 Баланс — реферальные начисления\n"
        "⚙️ Настройки подписки — управление подпиской\n\n"
        "Все суммы указаны в рублях.\n"
        f"Подписка включает {DEFAULT_DEVICE_LIMIT} устройств по умолчанию.\n"
        f"Дополнительное устройство — {EXTRA_DEVICE_PRICE_RUBLES / 30:.1f} ₽/день ({EXTRA_DEVICE_PRICE_RUBLES} ₽ за 30 дней).\n\n"
        "✉️ Написать нам в поддержку: @bravada_support"
    )


def text_fulfillment_success(active_until: str | None) -> str:
    lines = ["✅ Оплата получена!", "", "Ваша подписка активирована."]
    if active_until:
        lines.extend(["", f"📅 Действует до: {active_until}"])
    lines.extend(
        [
            "",
            "🔐 Нажмите «Мои ключи» для получения VPN-ключей.",
            "📎 Или «Ссылка для приложений» для автоматической настройки.",
        ]
    )
    return "\n".join(lines)


def text_error_generic() -> str:
    return "⚠️ Что-то пошло не так. Попробуйте позже."


def text_rate_limited() -> str:
    return "⏳ Слишком много запросов. Пожалуйста, подождите."


def text_balance_payment_success(active_until: str | None) -> str:
    lines = ["✅ Подписка оплачена с реферального баланса!", "", "Ваша подписка активирована."]
    if active_until:
        lines.extend(["", f"📅 Действует до: {active_until}"])
    lines.extend(["", "🔐 Нажмите «Мои ключи» для получения VPN-ключей."])
    return "\n".join(lines)


def text_balance_insufficient() -> str:
    return "❌ Недостаточно средств на балансе.\n\nПриглашайте друзей по реферальной ссылке, чтобы пополнить баланс."


def add_device_select_keyboard(current: int) -> dict[str, Any]:
    rows = [
        [
            {"text": "➖", "callback_data": f"{CB_ADD_DEV}{max(DEFAULT_DEVICE_LIMIT, current - 1)}"},
            {"text": f"Устройств: {current}", "callback_data": "noop"},
            {"text": "➕", "callback_data": f"{CB_ADD_DEV}{min(MAX_DEVICE_COUNT, current + 1)}"},
        ],
    ]
    if current > DEFAULT_DEVICE_LIMIT:
        rows.append(
            [{"text": f"✅ Подтвердить ({current} устройств)", "callback_data": f"{CB_ADD_DEV}confirm:{current}"}]
        )
    rows.append([{"text": "↩️ Назад", "callback_data": CB_SETTINGS}])
    return _inline_kb(rows)


def add_device_confirm_keyboard(new_count: int, *, balance_kopecks: int = 0, cost_kopecks: int = 0) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    if balance_kopecks >= cost_kopecks and cost_kopecks > 0:
        rows.append(
            [
                {
                    "text": f"💰 Оплатить с баланса ({cost_kopecks // 100} ₽)",
                    "callback_data": f"{CB_ADD_DEV_BALANCE}{new_count}",
                }
            ]
        )
    else:
        rows.append([{"text": "💳 Оплатить", "callback_data": f"add_dev_pay:{new_count}"}])
    rows.append([{"text": "↩️ Назад", "callback_data": CB_ADD_DEVICE}])
    return _inline_kb(rows)


def text_add_device_intro(current_count: int) -> str:
    lines = [
        "📱 Добавление устройств",
        "",
        f"Текущее количество: {current_count}",
        f"Стоимость: {EXTRA_DEVICE_PRICE_RUBLES / 30:.1f} ₽/день за каждое дополнительное устройство.",
        "",
        "Выберите количество устройств:",
    ]
    return "\n".join(lines)


def text_add_device_confirm(current_count: int, new_count: int, *, duration_days: int = 30) -> str:
    from app.domain.devices import extra_device_cost as _calc_cost

    extra = new_count - current_count
    cost = _calc_cost(new_count, current_count, duration_days)
    daily_price = EXTRA_DEVICE_PRICE_RUBLES / 30
    lines = [
        "📱 Подтверждение",
        "",
        f"Добавляем устройств: {extra}",
        f"Стоимость: {extra} × {daily_price:.1f} ₽/день × {duration_days} дн = {cost} ₽",
        "",
        "Устройства будут добавлены к текущей подписке.",
    ]
    return "\n".join(lines)


def text_add_device_success(new_count: int) -> str:
    return f"✅ Готово! Теперь у вас {new_count} устройств.\n\nНовое количество будет учтено при следующем продлении."


def text_add_device_unavailable() -> str:
    return "❌ У вас нет активной подписки.\n\nНажмите «🔑 Купить VPN» для оформления."


def remove_device_keyboard(current: int) -> dict[str, Any]:
    min_count = max(DEFAULT_DEVICE_LIMIT, current - 1)
    rows: list[list[dict[str, str]]] = []
    rows.append([{"text": f"📱 Текущее: {current} → {min_count}", "callback_data": "noop"}])
    if current > DEFAULT_DEVICE_LIMIT:
        rows.append(
            [{"text": f"✅ Подтвердить ({min_count} устройств)", "callback_data": f"remove_dev_confirm:{min_count}"}]
        )
    rows.append([{"text": "↩️ Назад", "callback_data": CB_SETTINGS}])
    return _inline_kb(rows)


def text_remove_device_confirm(current: int, new_count: int) -> str:
    return (
        f"📱 Снижение устройств\n\n"
        f"Текущее: {current}\n"
        f"Новое: {new_count}\n\n"
        f"Новое количество будет учтено при следующем продлении."
    )


def text_remove_device_success(new_count: int) -> str:
    return f"✅ Готово! Теперь у вас {new_count} устройств."


# ─── Email linking ──────────────────────────────────────────────────


def link_email_keyboard() -> dict[str, Any]:
    return _inline_kb([[{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}]])


def link_email_code_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "📧 Отправить код повторно", "callback_data": CB_RESEND_EMAIL_CODE}],
            [{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}],
        ]
    )


def text_link_email_intro(email: str | None = None) -> str:
    if email:
        return (
            "📧 Привязка email\n\n"
            f"Текущий email: {email}\n\n"
            "Если хотите заменить, введите новый email.\n"
            "Для подтверждения на почту будет отправлен код."
        )
    return "📧 Привязка email\n\nВведите ваш email-адрес.\nНа него будет отправлен код подтверждения."


def text_link_email_code_sent(email: str) -> str:
    return f"📧 Код отправлен на {email}\n\nВведите 6-значный код из письма.\nКод действителен 10 минут."


def text_link_email_success(email: str) -> str:
    return f"✅ Email {email} успешно привязан!\n\nТеперь вы можете входить на сайт используя этот email."


def text_link_email_already_linked(email: str) -> str:
    return f"ℹ️ К вашему аккаунту уже привязан email: {email}"


def text_link_email_error(error: str) -> str:
    messages = {
        "invalid_email": "❌ Некорректный email. Попробуйте снова.",
        "email_already_linked": "ℹ️ Этот email уже привязан к вашему аккаунту.",
        "email_belongs_to_other_account": "❌ Этот email привязан к другому аккаунту.",
        "rate_limited": "⏳ Слишком много попыток. Попробуйте позже.",
        "invalid_code": "❌ Неверный код. Попробуйте снова.",
        "code_expired": "❌ Код истёк. Запросите новый.",
        "too_many_attempts": "❌ Слишком много неверных попыток. Запросите новый код.",
        "smtp_not_configured": "⚠️ Отправка писем временно недоступна. Попробуйте позже.",
    }
    return messages.get(error, "⚠️ Что-то пошло не так. Попробуйте позже.")


# ─── Trial period ──────────────────────────────────────────────────


def text_trial_offer() -> str:
    return (
        "🎁 Попробуйте VPN бесплатно!\n\n"
        "3 дня полного доступа ко всем серверам.\n"
        "Ключи для всех стран, ссылка для автонастройки в Karing, Happ, v2rayTune.\n\n"
        "Без обязательств — просто попробуйте."
    )


def trial_offer_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "🎁 Попробовать 3 дня бесплатно", "callback_data": CB_TRIAL}],
            [{"text": "🔑 Купить VPN", "callback_data": CB_BUY_VPN}],
        ]
    )


def text_trial_activated(config: VlessUserConfig) -> str:
    lines = [
        "🎉 Пробный период активирован!\n",
        "⏳ Действует 3 дня.\n",
        "📎 Ссылка для подписки (нажмите, чтобы скопировать):",
        f"`{config.subscription_url}`\n",
        "🔑 Ключи:",
    ]
    lines.append(format_key_list(config.servers))
    lines.extend(
        [
            "",
            "💡 Как подключиться:",
            "1. Скачайте Karing / Happ / v2rayTune",
            "2. Нажмите на ссылку выше — она скопируется",
            "3. В приложении: Подписка → Вставить ссылку → Импорт",
            "4. Выберите сервер и подключитесь!",
        ]
    )
    return "\n".join(lines)


def trial_activated_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "📱 Подключить устройство", "callback_data": CB_CONNECT_DEVICE}],
            [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
        ]
    )


# ─── Custom days flow ──────────────────────────────────────────────


def text_custom_days_prompt() -> str:
    return (
        "📦 Свой тариф\n\n"
        "Введите количество дней (от 1 до 365):\n"
        f"Стоимость: {CUSTOM_DAY_PRICE_RUBLES} ₽ за каждый день.\n\n"
        f"Пример: 45 → 45 дней за {45 * CUSTOM_DAY_PRICE_RUBLES} ₽"
    )


def custom_days_prompt_keyboard() -> dict[str, Any]:
    return _inline_kb([[{"text": "↩️ Назад к тарифам", "callback_data": CB_BUY_VPN}]])


def text_custom_days_invalid(user_input: str) -> str:
    return f"❌ «{user_input}» — некорректное значение.\n\nВведите целое число от 1 до 365."
