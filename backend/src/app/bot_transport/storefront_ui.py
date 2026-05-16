"""Новый UI Telegram-бота: inline-клавиатуры, тексты на русском, callback-навигация.

Создан как отдельный слой поверх существующего message_catalog/outbound.
Параллельно работает со старым UI, не ломает существующие тесты.
"""

from __future__ import annotations

from typing import Any

from app.application.purchase_handler import PurchasePlanOption, PurchaseSummary
from app.application.referral_handler import ReferralBalanceInfo, ReferralInfo
from app.domain.devices import DEFAULT_DEVICE_LIMIT, EXTRA_DEVICE_PRICE_RUBLES, MAX_DEVICE_COUNT
from app.domain.plans import plan_display_name
from app.issuance.vless_provider import VlessUserConfig, format_key_list

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


# ─── Inline keyboards ──────────────────────────────────────────────────


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
            [{"text": "📎 Ссылка для приложений", "callback_data": CB_SUB_URL}],
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


def buy_vpn_keyboard(plans: tuple[PurchasePlanOption, ...]) -> dict[str, Any]:
    rows = [
        [{"text": f"{p.display_name} — {p.price_rubles} ₽", "callback_data": f"{CB_PLAN}{p.plan_id}"}] for p in plans
    ]
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


def text_welcome() -> str:
    return "👋 Добро пожаловать в VPN-сервис!\n\nВыберите действие в меню ниже."


def text_main_menu() -> str:
    return "🏠 Главное меню"


def text_buy_vpn_intro() -> str:
    return "🔑 Выберите тариф VPN-подписки:"


def text_device_select(plan_id: str, price_rubles: int, duration_months: int, device_count: int) -> str:
    from app.domain.devices import extra_device_cost, extra_device_count

    extra = extra_device_count(device_count)
    extra_cost = extra_device_cost(device_count, duration_months=duration_months)
    total = price_rubles + extra_cost
    lines = [
        f"📦 Тариф: {plan_display_name(plan_id)}",
        f"💰 Базовая цена: {price_rubles} ₽",
        f"📱 Устройств: {device_count}",
    ]
    if extra > 0:
        lines.append(
            f"  ➕ Доп. устройств: {extra} × {EXTRA_DEVICE_PRICE_RUBLES} ₽/мес × {duration_months} мес = {extra_cost} ₽"
        )
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
        lines.append(
            f"  Доп. устройств: {summary.extra_devices} × {EXTRA_DEVICE_PRICE_RUBLES} ₽/мес × {summary.duration_months} мес = {summary.extra_device_cost_rubles} ₽"
        )
    lines.extend(["", f"💳 К оплате: {summary.total_price_rubles} ₽", "", "Нажмите «Оплатить» для перехода к оплате."])
    return "\n".join(lines)


def text_payment_unavailable() -> str:
    return "⚠️ Оплата временно недоступна. Попробуйте позже или обратитесь в поддержку."


def text_subscription_active(active_until: str | None, plan_name: str | None, device_count: int | None) -> str:
    lines = ["✅ Ваша подписка активна!"]
    if plan_name:
        lines.append(f"📦 Тариф: {plan_name}")
    if device_count is not None:
        lines.append(f"📱 Устройств: {device_count}")
    if active_until:
        lines.append(f"📅 Действует до: {active_until}")
    lines.extend(["", "Используйте кнопки меню для управления."])
    return "\n".join(lines)


def text_subscription_expired() -> str:
    return "❌ Ваша подписка истекла.\n\nДля продления нажмите «🔑 Купить VPN»."


def text_no_subscription() -> str:
    return "У вас нет активной подписки.\n\nНажмите «🔑 Купить VPN» для оформления."


def text_my_keys(config: VlessUserConfig) -> str:
    lines = ["🔐 Ваши VLESS-ключи:\n"]
    lines.append(format_key_list(config.servers))
    lines.extend(["", "💡 Нажмите на ключ ниже, чтобы скопировать.", "Поддерживаемые приложения: Karing, v2rayTune, Happ, v2rayNG и др."])
    return "\n".join(lines)


def keys_keyboard() -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    rows.append([{"text": "🔄 Перевыпустить ключи", "callback_data": CB_REISSUE_KEYS}])
    rows.append([{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}])
    return _inline_kb(rows)


def text_reissue_confirm() -> str:
    return (
        "⚠️ Перевыпуск ключей\n\n"
        "Старые ключи перестанут работать.\n"
        "Все устройства нужно будет переподключить.\n\n"
        "Продолжить?"
    )


def reissue_confirm_keyboard() -> dict[str, Any]:
    return _inline_kb([
        [{"text": "✅ Да, перевыпустить", "callback_data": CB_REISSUE_CONFIRM}],
        [{"text": "↩️ Назад", "callback_data": CB_MY_KEYS}],
    ])


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
        "  1 мес — 35% | 3 мес — 30% | 6 мес — 25%\n"
        "Со 2-го уровня:\n"
        "  1 мес — 5% | 3 мес — 3% | 6 мес — 2%\n\n"
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
        f"Дополнительное устройство — {EXTRA_DEVICE_PRICE_RUBLES} ₽ за каждый месяц подписки.\n\n"
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
        f"Стоимость: {EXTRA_DEVICE_PRICE_RUBLES} ₽ за каждое дополнительное устройство.",
        "",
        "Выберите количество устройств:",
    ]
    return "\n".join(lines)


def text_add_device_confirm(current_count: int, new_count: int) -> str:
    extra = new_count - current_count
    cost = extra * EXTRA_DEVICE_PRICE_RUBLES
    lines = [
        "📱 Подтверждение",
        "",
        f"Добавляем устройств: {extra}",
        f"Стоимость: {extra} × {EXTRA_DEVICE_PRICE_RUBLES} ₽ = {cost} ₽",
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
    return (
        "📧 Привязка email\n\n"
        "Введите ваш email-адрес.\n"
        "На него будет отправлен код подтверждения."
    )


def text_link_email_code_sent(email: str) -> str:
    return (
        f"📧 Код отправлен на {email}\n\n"
        "Введите 6-значный код из письма.\n"
        "Код действителен 10 минут."
    )


def text_link_email_success(email: str) -> str:
    return (
        f"✅ Email {email} успешно привязан!\n\n"
        "Теперь вы можете входить на сайт используя этот email."
    )


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
