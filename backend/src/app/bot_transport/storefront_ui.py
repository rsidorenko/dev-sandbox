"""Новый UI Telegram-бота: inline-клавиатуры, тексты на русском, callback-навигация.

Создан как отдельный слой поверх существующего message_catalog/outbound.
Параллельно работает со старым UI, не ломает существующие тесты.
"""

from __future__ import annotations

from typing import Any

from app.application.purchase_handler import PurchasePlanOption, PurchaseSummary
from app.application.referral_handler import ReferralBalanceInfo, ReferralInfo
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


# ─── Inline keyboards ──────────────────────────────────────────────────

def _inline_kb(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": rows}


def main_menu_keyboard() -> dict[str, Any]:
    return _inline_kb([
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
        [
            {"text": "⚙️ Настройки подписки", "callback_data": CB_SETTINGS},
            {"text": "❓ Помощь", "callback_data": CB_HELP},
        ],
    ])


def buy_vpn_keyboard(plans: tuple[PurchasePlanOption, ...]) -> dict[str, Any]:
    rows = []
    for p in plans:
        rows.append([{"text": f"{p.display_name} — {p.price_rubles} ₽", "callback_data": f"{CB_PLAN}{p.plan_id}"}])
    rows.append([{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}])
    return _inline_kb(rows)


def device_select_keyboard(plan_id: str, current: int = 5) -> dict[str, Any]:
    rows = [
        [
            {"text": "➖", "callback_data": f"{CB_DEVICES}{plan_id}:{max(1, current - 1)}"},
            {"text": f"Устройств: {current}", "callback_data": "noop"},
            {"text": "➕", "callback_data": f"{CB_DEVICES}{plan_id}:{min(20, current + 1)}"},
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
        rows.append([{"text": f"💰 С баланса ({balance_kopecks // 100} ₽)", "callback_data": f"{CB_PAY_BALANCE}{plan_id}:{device_count}"}])
    rows.append([{"text": "↩️ Назад", "callback_data": f"{CB_DEVICES}{plan_id}:{device_count}"}])
    return _inline_kb(rows)


def back_only_keyboard(callback: str = CB_MAIN_MENU) -> dict[str, Any]:
    return _inline_kb([[{"text": "↩️ Назад", "callback_data": callback}]])


def settings_keyboard(has_subscription: bool) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    if has_subscription:
        rows.append([{"text": "➕ Добавить устройство (+80 ₽)", "callback_data": "add_device"}])
    rows.append([{"text": "📶 Конфиг для роутера (Скоро)", "callback_data": CB_ROUTER}])
    rows.append([{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}])
    return _inline_kb(rows)


def balance_pay_keyboard(balance_kopecks: int, plans: tuple[PurchasePlanOption, ...]) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    for p in plans:
        price_k = p.price_rubles * 100
        if balance_kopecks >= price_k:
            rows.append([{"text": f"{p.display_name} ({p.price_rubles} ₽)", "callback_data": f"{CB_PAY_BALANCE}{p.plan_id}"}])
    if not rows:
        rows.append([{"text": "Недостаточно средств", "callback_data": "noop"}])
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
    from app.domain.devices import extra_device_count, extra_device_cost
    extra = extra_device_count(device_count)
    extra_cost = extra_device_cost(device_count, duration_months=duration_months)
    total = price_rubles + extra_cost
    lines = [
        f"📦 Тариф: {plan_display_name(plan_id)}",
        f"💰 Базовая цена: {price_rubles} ₽",
        f"📱 Устройств: {device_count}",
    ]
    if extra > 0:
        per_device_per_month = 80
        lines.append(f"  ➕ Доп. устройств: {extra} × {per_device_per_month} ₽/мес × {duration_months} мес = {extra_cost} ₽")
    lines.append(f"\n💳 Итого: {total} ₽")
    lines.append("\nВыберите количество устройств:")
    return "\n".join(lines)


def text_purchase_summary(summary: PurchaseSummary) -> str:
    lines = [
        "📋 Заказ:", "",
        f"  Тариф: {summary.plan_display_name}",
        f"  Устройств: {summary.device_count}",
    ]
    if summary.extra_devices > 0:
        lines.append(f"  Доп. устройств: {summary.extra_devices} × 80 ₽/мес × {summary.duration_months} мес = {summary.extra_device_cost_rubles} ₽")
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
    lines.extend(["", "💡 Скопируйте ключ и вставьте в приложение:", "Karing, v2rayTune, Happ, v2rayNG и др."])
    return "\n".join(lines)


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
    return f"💰 Ваш баланс: {info.balance_rubles:.2f} ₽\n\nЭтими деньгами можно оплатить подписку."


def text_settings(has_subscription: bool) -> str:
    lines = ["⚙️ Настройки подписки\n"]
    if has_subscription:
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
        "Подписка включает 5 устройств по умолчанию.\n"
        "Дополнительное устройство — 80 ₽ за каждый месяц подписки."
    )


def text_fulfillment_success(active_until: str | None) -> str:
    lines = ["✅ Оплата получена!", "", "Ваша подписка активирована."]
    if active_until:
        lines.extend(["", f"📅 Действует до: {active_until}"])
    lines.extend(["", "🔐 Нажмите «Мои ключи» для получения VPN-ключей.", "📎 Или «Ссылка для приложений» для автоматической настройки."])
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


def text_balance_payment_error() -> str:
    return "⚠️ Не удалось оплатить с баланса. Попробуйте позже."


def add_device_select_keyboard(current: int) -> dict[str, Any]:
    rows = [
        [
            {"text": "➖", "callback_data": f"{CB_ADD_DEV}{max(5, current - 1)}"},
            {"text": f"Устройств: {current}", "callback_data": "noop"},
            {"text": "➕", "callback_data": f"{CB_ADD_DEV}{min(20, current + 1)}"},
        ],
    ]
    if current > 5:
        rows.append([{"text": f"✅ Подтвердить ({current} устройств)", "callback_data": f"{CB_ADD_DEV}confirm:{current}"}])
    rows.append([{"text": "↩️ Назад", "callback_data": CB_SETTINGS}])
    return _inline_kb(rows)


def add_device_confirm_keyboard(new_count: int, *, balance_kopecks: int = 0, cost_kopecks: int = 0) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    if balance_kopecks >= cost_kopecks and cost_kopecks > 0:
        rows.append([{"text": f"💰 Оплатить с баланса ({cost_kopecks // 100} ₽)", "callback_data": f"{CB_ADD_DEV_BALANCE}{new_count}"}])
    else:
        rows.append([{"text": "💳 Оплатить", "callback_data": f"add_dev_pay:{new_count}"}])
    rows.append([{"text": "↩️ Назад", "callback_data": CB_ADD_DEVICE}])
    return _inline_kb(rows)


def text_add_device_intro(current_count: int) -> str:
    lines = [
        "📱 Добавление устройств",
        "",
        f"Текущее количество: {current_count}",
        "Стоимость: 80 ₽ за каждое дополнительное устройство.",
        "",
        "Выберите количество устройств:",
    ]
    return "\n".join(lines)


def text_add_device_confirm(current_count: int, new_count: int) -> str:
    extra = new_count - current_count
    cost = extra * 80
    lines = [
        "📱 Подтверждение",
        "",
        f"Добавляем устройств: {extra}",
        f"Стоимость: {extra} × 80 ₽ = {cost} ₽",
        "",
        "Устройства будут добавлены к текущей подписке.",
    ]
    return "\n".join(lines)


def text_add_device_success(new_count: int) -> str:
    return f"✅ Готово! Теперь у вас {new_count} устройств.\n\nНовое количество будет учтено при следующем продлении."


def text_add_device_unavailable() -> str:
    return "❌ У вас нет активной подписки.\n\nНажмите «🔑 Купить VPN» для оформления."
