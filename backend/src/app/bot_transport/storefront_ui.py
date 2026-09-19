"""Новый UI Telegram-бота: inline-клавиатуры, тексты на русском, callback-навигация.

Создан как отдельный слой поверх существующего message_catalog/outbound.
Параллельно работает со старым UI, не ломает существующие тесты.
"""

from __future__ import annotations

import json
import os
import time
from html import escape
from typing import Any
from urllib.parse import quote, urlencode

from app.application.connection_check import connection_check_enabled
from app.application.purchase_handler import PurchasePlanOption, PurchaseSummary
from app.application.referral_handler import ReferralBalanceInfo, ReferralInfo
from app.application.trial_channel_gate import channel_username_from_env
from app.application.trial_feedback import CONNECTION_PROBLEM_REASONS, TRIAL_NONPAYMENT_REASONS
from app.domain.devices import DEFAULT_DEVICE_LIMIT, EXTRA_DEVICE_PRICE_RUBLES, MAX_DEVICE_COUNT
from app.domain.plans import CUSTOM_DAY_PRICE_RUBLES, plan_display_name
from app.domain.pricing import (
    WINNER_GLOBAL,
    WINNER_PROMO,
    GlobalDiscount,
    effective_plan_price,
    global_discount_label,
)
from app.domain.promo import PromoCode, PromoDiscountKind
from app.issuance.vless_provider import VlessServerConfig, VlessUserConfig, format_key_list
from app.shared.site_url import get_site_base_url

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
CB_DO_PAY_CRYPTO = "pay_crypto:"
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
CB_CONNECT_FAST_WIN = "connect_fast_win"
CB_CONNECT_FAST_ANDROID = "connect_fast_android"
CB_CONNECT_FAST_IOS = "connect_fast_ios"
CB_CONNECT_FAST_MAC = "connect_fast_mac"
CB_CONNECT_FAST_PROBLEM = "connect_fast_problem:"
CB_TRIAL_FEEDBACK_NONPAY = "trial_feedback_nonpay"
CB_TRIAL_FEEDBACK_ANSWER = "trial_fb:"
CB_CONNECT_NEXT = "connect_next"
CB_CONNECT_DONE = "connect_done"
CB_TRIAL = "start_trial"
# Гейт подписки на канал перед выдачей бесплатных выгод: «Я подписался — проверить».
CB_CHECK_SUB = "check_sub"
CB_ALL_KEYS = "all_keys"
CB_SERVER = "server:"
CB_CUSTOM_DAYS = "custom_days"
CB_PROMOCODE = "enter_promo"
# Ввод промокода ИЗ сводки покупки (несёт plan:devices, чтобы после ввода вернуться к заказу).
CB_PROMO_AT = "enter_promo:"
# Бонус-кнопка рассылки: callback_data = "bc_days:{job_id}". Несёт только job_id —
# параметры бонуса (дни) хранятся в broadcast_bonus_claims, не в callback (приватность + 64-байт лимит).
CB_BROADCAST_DAYS = "bc_days:"
# Кнопка промокода в рассылке: callback_data = "bc_promo:{job_id}". Несёт только job_id —
# код хранится в broadcast_promo_claims, не в callback (приватность + 64-байт лимит: 9 + UUID = 45 байт).
CB_BROADCAST_PROMO = "bc_promo:"
CB_IOS_STEP = "ios_step:"
CB_IOS_YES = "ios_yes"
CB_IOS_NO = "ios_no"
CB_IOS_RETRY = "ios_retry"
CB_IOS_DID_WORK = "ios_did_work"
CB_MAC_STEP = "mac_step:"
CB_MAC_YES = "mac_yes"
CB_MAC_NO = "mac_no"
CB_MAC_RETRY = "mac_retry"
CB_MAC_DID_WORK = "mac_did_work"
CB_CONNECT_TV = "connect_tv"
CB_TV_STEP = "tv_step:"
CB_TV_YES = "tv_yes"
CB_TV_NO = "tv_no"
CB_TV_RETRY = "tv_retry"
CB_TV_DID_WORK = "tv_did_work"
CB_WIN_STEP = "win_step:"
CB_WIN_YES = "win_yes"
CB_WIN_NO = "win_no"
CB_WIN_RETRY = "win_retry"
CB_WIN_DID_WORK = "win_did_work"
CB_ANDROID_STEP = "android_step:"
CB_ANDROID_YES = "android_yes"
CB_ANDROID_NO = "android_no"
CB_ANDROID_RETRY = "android_retry"
CB_ANDROID_DID_WORK = "android_did_work"
# Happ step callbacks (parallel to the Karing *_STEP flows above). Happ is the
# recommended app for ios/android/mac because whitelist bypass only works there
# (see web_api/subscription.py: surface='happ' is served only to the Happ client).
# The Karing flows (CB_IOS_STEP/CB_MAC_STEP/CB_ANDROID_STEP) stay untouched.
CB_IOS_HAPP_STEP = "ios_happ_step:"
CB_MAC_HAPP_STEP = "mac_happ_step:"
CB_ANDROID_HAPP_STEP = "android_happ_step:"


# ─── Inline keyboards ──────────────────────────────────────────────────

_MAX_CALLBACK_BYTES = 64


def _cb(prefix: str, value: str) -> str:
    """Build callback_data with Telegram 64-byte limit enforcement."""
    payload = f"{prefix}{value}"
    encoded = payload.encode("utf-8")
    if len(encoded) > _MAX_CALLBACK_BYTES:
        return payload[: _MAX_CALLBACK_BYTES - 3] + "..."
    return payload


def _inline_kb(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {"inline_keyboard": rows}


def main_menu_keyboard(*, trial_available: bool = False) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    # Deferrable trial: surface a prominent "take trial" button at the very top while the
    # user hasn't used their one-time trial yet. Hidden once trial_used / active subscription.
    if trial_available:
        rows.append([{"text": "🎁 Забрать 3 дня бесплатно", "callback_data": CB_TRIAL}])
    rows.extend(
        [
            # style — нативное поле Telegram Bot API (InlineKeyboardButton.style):
            # "success"=зелёная, "primary"=синяя, "danger"=красная. Выделяем целевые
            # действия. На старых клиентах поле игнорируется — кнопка остаётся рабочей.
            [{"text": "🔑 Купить VPN", "callback_data": CB_BUY_VPN, "style": "success"}],
            [{"text": "📱 Подключить устройство", "callback_data": CB_CONNECT_DEVICE, "style": "primary"}],
            # «Мои ключи», «Настройки подписки», «Привязать email» перенесены в
            # подменю «Моя подписка» (my_subscription_keyboard) — туда же ведёт Назад
            # с этих экранов (контекстный возврат «откуда пришёл»). Кнопка нейтральная
            # (без style) и идёт после целевых действий — по референсу главного меню.
            [{"text": "👤 Моя подписка", "callback_data": CB_MY_SUB}],
            [
                {"text": "👥 Пригласить друга", "callback_data": CB_REFERRAL},
                {"text": "💰 Баланс", "callback_data": CB_BALANCE},
            ],
            [
                {"text": "🌐 Наш сайт", "url": "https://bravada-connect.ru/"},
                {"text": "❓ Помощь", "callback_data": CB_HELP},
            ],
            [{"text": "🎟️ Ввести промокод", "callback_data": CB_PROMOCODE}],
        ]
    )
    return _inline_kb(rows)


def my_subscription_keyboard() -> dict[str, Any]:
    """Подменю «Моя подписка»: ключи / настройки / email + Назад в главное меню.

    Кнопки «Мои ключи», «Настройки подписки», «Привязать email» живут здесь (а не в
    главном меню); их Назад ведёт обратно сюда (контекстный возврат «откуда пришёл»).
    """
    return _inline_kb(
        [
            [{"text": "🔐 Мои ключи", "callback_data": CB_MY_KEYS}],
            [{"text": "⚙️ Настройки подписки", "callback_data": CB_SETTINGS}],
            [{"text": "📧 Привязать email", "callback_data": CB_LINK_EMAIL}],
            [{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}],
        ]
    )


_PLAN_EMOJI: dict[str, str] = {
    # Шкала «путешествие»: быстрая проба → надолго. Заменяет прежние цветные
    # шарики (⚪🟡🟠🟢🔵🟣), которые не несли смысла.
    "1d": "⚡",
    "7d": "🚀",
    "14d": "✈️",
    "1m": "🛫",
    "3m": "⭐",
    "6m": "💎",
    "365d": "👑",
}


def buy_vpn_keyboard(
    plans: tuple[PurchasePlanOption, ...],
    *,
    promo: PromoCode | None = None,
    global_discount: GlobalDiscount | None = None,
) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    for p in plans:
        base = p.price_rubles
        # Скидка для этого тарифа — best-of(промокод, глобальная). Per-tariff промокод
        # даёт 0 на тарифе вне map → тогда (и при отсутствии промокода) решает глобальная.
        discounted = effective_plan_price(base, plan_id=p.plan_id, promo=promo, global_discount=global_discount)
        if discounted.discounted_rubles < base:
            label = (
                f"{_PLAN_EMOJI.get(p.plan_id, '📦')} {p.display_name} — {discounted.discounted_rubles} ₽ вместо {base}"
            )
        else:
            label = f"{_PLAN_EMOJI.get(p.plan_id, '📦')} {p.display_name} — {base} ₽"
        rows.append([{"text": label, "callback_data": f"{CB_PLAN}{p.plan_id}"}])
    rows.append([{"text": "📦 Свой тариф", "callback_data": CB_CUSTOM_DAYS}])
    rows.append([{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}])
    return _inline_kb(rows)


def device_select_keyboard(plan_id: str, current: int = DEFAULT_DEVICE_LIMIT) -> dict[str, Any]:
    rows = [
        [
            {"text": "➖", "callback_data": f"{CB_DEVICES}{plan_id}:{max(DEFAULT_DEVICE_LIMIT, current - 1)}"},
            {"text": f"Устройств: {current}", "callback_data": "noop"},
            {"text": "➕", "callback_data": f"{CB_DEVICES}{plan_id}:{min(MAX_DEVICE_COUNT, current + 1)}"},
        ],
        [{"text": f"✅ Продолжить ({current} устройств)", "callback_data": f"{CB_CONFIRM_PAY}{plan_id}:{current}"}],
        [{"text": "↩️ Назад к тарифам", "callback_data": CB_BUY_VPN}],
    ]
    return _inline_kb(rows)


def _cryptobot_enabled() -> bool:
    return os.environ.get("CRYPTOBOT_ENABLED", "").strip().lower() in ("1", "true", "yes")


def confirm_pay_keyboard(
    plan_id: str,
    device_count: int,
    *,
    balance_kopecks: int = 0,
    total_kopecks: int = 0,
    promo: PromoCode | None = None,
) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = [
        [{"text": "💳 Оплатить", "callback_data": f"{CB_DO_PAY}{plan_id}:{device_count}"}],
    ]
    if _cryptobot_enabled():
        rows.append([{"text": "₮ Оплатить криптой", "callback_data": f"{CB_DO_PAY_CRYPTO}{plan_id}:{device_count}"}])
    if balance_kopecks >= total_kopecks and total_kopecks > 0:
        rows.append(
            [
                {
                    "text": f"💰 С баланса ({balance_kopecks // 100} ₽)",
                    "callback_data": f"{CB_PAY_BALANCE}{plan_id}:{device_count}",
                }
            ]
        )
    # Промокод: точка ввода внутри оплаты (Фаза 4). После ввода FSM возвращает к этой сводке.
    if promo is not None:
        rows.append(
            [
                {
                    "text": f"🎁 {promo.code} ({promo_badge_label(promo)}) — изменить",
                    "callback_data": f"{CB_PROMO_AT}{plan_id}:{device_count}",
                }
            ]
        )
    else:
        rows.append([{"text": "🎟️ Ввести промокод", "callback_data": f"{CB_PROMO_AT}{plan_id}:{device_count}"}])
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
    # Назад ведёт в «Моя подписку» (контекстный возврат: единственный путь сюда — из подменю).
    rows.append([{"text": "↩️ Назад", "callback_data": CB_MY_SUB}])
    return _inline_kb(rows)


# ─── Message texts ──────────────────────────────────────────────────────


def text_welcome(*, trial_available: bool = False) -> str:
    base = _text_intro()
    if trial_available:
        base += "\n\n🎁 Вам доступен бесплатный пробный период — 3 дня!"
    return base


def _text_intro() -> str:
    """Приветствие с бенефитами — единый текст welcome и главного меню."""
    return (
        "<b>Привет! Я — Bravada VPN 🤖</b>\n\n"
        "Помогу подключить VPN за пару минут.\n\n"
        "⚡ Быстрое подключение\n"
        "🔐 Защищённое соединение\n"
        "🌍 Доступ к нужным сайтам"
    )


def welcome_keyboard(*, trial_available: bool = False) -> dict[str, Any]:
    if trial_available:
        return _inline_kb(
            [
                [{"text": "🎁 Попробовать 3 дня бесплатно", "callback_data": CB_TRIAL}],
                [{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}],
            ]
        )
    return main_menu_keyboard()


# ─── Гейт подписки на канал перед выдачей бесплатных выгод ────────────


def channel_subscribe_gate_keyboard(
    channel_username: str,
    *,
    retry_callback_data: str,
    back_callback_data: str = CB_MAIN_MENU,
) -> dict[str, Any]:
    """Клавиатура проверки подписки с возвратом к исходному действию."""
    return _inline_kb(
        [
            [{"text": "📢 Подписаться на канал", "url": f"https://t.me/{channel_username.lstrip('@')}"}],
            [{"text": "🔄 Я подписался — проверить", "callback_data": retry_callback_data}],
            [{"text": "↩️ Назад", "callback_data": back_callback_data}],
        ]
    )


def trial_subscribe_gate_keyboard(channel_username: str) -> dict[str, Any]:
    """Совместимый shortcut для экрана пробного периода."""
    return channel_subscribe_gate_keyboard(channel_username, retry_callback_data=CB_CHECK_SUB)


def text_trial_subscribe_required(_channel_username: str) -> str:
    return "<b>🎁 Пробный период — для подписчиков канала</b>\n\nПодпишись на наш канал"


def text_trial_subscribe_still_missing() -> str:
    return (
        "😕 Пока не видим вашу подписку.\n\n"
        "Убедитесь, что вы подписались на канал, и нажмите «🔄 Я подписался — проверить» ещё раз."
    )


def text_bonus_subscribe_required() -> str:
    return "<b>🎁 Подарки и промокоды — для подписчиков канала</b>\n\nПодпишись на наш канал"


def text_bonus_subscribe_still_missing() -> str:
    return (
        "😕 Пока не видим вашу подписку.\n\n"
        "Убедитесь, что вы подписались на канал, и нажмите «🔄 Я подписался — проверить» ещё раз."
    )


def text_channel_subscription_check_failed() -> str:
    return (
        "⚠️ Сейчас не удалось проверить подписку на канал.\n\n"
        "Попробуйте нажать «🔄 Я подписался — проверить» ещё раз немного позже."
    )


def text_main_menu() -> str:
    return _text_intro()


def text_buy_vpn_intro(
    *,
    promo: PromoCode | None = None,
    global_discount: GlobalDiscount | None = None,
) -> str:
    lines = [
        "<b>🔑 Выберите тариф VPN-подписки:</b>",
        "",
        "⚡ — 1 день",
        "🚀 — 7 дней",
        "✈️ — 2 недели",
        "🛫 — 1 месяц",
        "⭐ — 3 месяца",
        "💎 — 6 месяцев",
        "👑 — 1 год",
        "",
        "Или выберите «📦 Свой тариф» для произвольного количества дней.",
    ]
    if promo is not None:
        lines.append("")
        lines.append(f"🎁 <b>Промокод {promo.code} применён</b> — цены ниже уже со скидкой.")
    if global_discount is not None and global_discount.is_active:
        lines.append("")
        lines.append(f"🎁 <b>Акция {global_discount_label(global_discount)}</b> — цены ниже уже со скидкой.")
    return "\n".join(lines)


def text_device_select(
    plan_id: str,
    price_rubles: int,
    duration_days: int,
    device_count: int,
    *,
    promo: PromoCode | None = None,
    global_discount: GlobalDiscount | None = None,
) -> str:
    from app.domain.devices import extra_device_cost, extra_device_count

    extra = extra_device_count(device_count)
    extra_cost = extra_device_cost(device_count, duration_days=duration_days)
    total = price_rubles + extra_cost
    lines = [
        f"<b>📦 Тариф: {plan_display_name(plan_id)}</b>",
        f"💰 Базовая цена: {price_rubles} ₽",
        f"📱 Устройств: {device_count}",
    ]
    if extra > 0:
        daily_price = EXTRA_DEVICE_PRICE_RUBLES / 30
        lines.append(f"  ➕ Доп. устройств: {extra} × {daily_price:.1f} ₽/день × {duration_days} дн = {extra_cost} ₽")
    # Скидка действует на весь итог подписки (base + доп. устройства), т.к. это покупка
    # подписки (is_addon=False). Итог — best-of(промокод, глобальная); строка — по winner:
    # пер-tariff промокод с 0 скидки НЕ печатает «−0 ₽» (тогда решает глобальная).
    eff = effective_plan_price(total, plan_id=plan_id, promo=promo, global_discount=global_discount)
    if eff.winner == WINNER_PROMO and promo is not None:
        lines.append(f"  🎁 Промокод {promo.code}: −{total - eff.discounted_rubles} ₽")
        lines.append(f"\n💳 Итого: <b>{eff.discounted_rubles} ₽</b> (вместо {total})")
    elif eff.winner == WINNER_GLOBAL and global_discount is not None:
        lines.append(f"  🎁 Акция {global_discount_label(global_discount)}: −{total - eff.discounted_rubles} ₽")
        lines.append(f"\n💳 Итого: <b>{eff.discounted_rubles} ₽</b> (вместо {total})")
    else:
        lines.append(f"\n💳 Итого: {total} ₽")
    lines.append("\nВыберите количество устройств:")
    return "\n".join(lines)


def text_purchase_summary(
    summary: PurchaseSummary,
    *,
    promo: PromoCode | None = None,
    promo_note: str = "",
    global_discount: GlobalDiscount | None = None,
) -> str:
    lines = ["<b>📋 Заказ:</b>", ""]
    if promo_note:
        lines += [promo_note, ""]
    lines += [
        f"  Тариф: {summary.plan_display_name}",
        f"  Устройств: {summary.device_count}",
    ]
    if summary.extra_devices > 0:
        daily_price = EXTRA_DEVICE_PRICE_RUBLES / 30
        lines.append(
            f"  Доп. устройств: {summary.extra_devices} × {daily_price:.1f} ₽/день × {summary.duration_days} дн = {summary.extra_device_cost_rubles} ₽"
        )
    base = summary.total_price_rubles
    # Итог — best-of(промокод, глобальная), строка — по winner: при победе глобальной
    # промокод не печатает «−0 ₽» (per-tariff промокод с нулевой скидкой на тарифе).
    eff = effective_plan_price(base, plan_id=summary.plan_id, promo=promo, global_discount=global_discount)
    if eff.winner == WINNER_PROMO and promo is not None:
        lines += ["", f"  🎁 Промокод {promo.code}: {promo_badge_label(promo)}"]
        lines.append(f"  Скидка: {base - eff.discounted_rubles} ₽")
        lines += [
            "",
            f"💳 К оплате: {eff.discounted_rubles} ₽ (вместо {base} ₽)",
            "",
            "Нажмите «Оплатить» для перехода к оплате.",
        ]
    elif eff.winner == WINNER_GLOBAL and global_discount is not None:
        lines += ["", f"  🎁 Акция: {global_discount_label(global_discount)}"]
        lines.append(f"  Скидка: {base - eff.discounted_rubles} ₽")
        lines += [
            "",
            f"💳 К оплате: {eff.discounted_rubles} ₽ (вместо {base} ₽)",
            "",
            "Нажмите «Оплатить» для перехода к оплате.",
        ]
    else:
        lines.extend(["", f"💳 К оплате: {base} ₽", "", "Нажмите «Оплатить» для перехода к оплате."])
    return "\n".join(lines)


def text_payment_unavailable() -> str:
    return "⚠️ Оплата временно недоступна. Попробуйте позже или обратитесь в поддержку."


def _format_traffic(bytes_total: int) -> str:
    """Человекочитаемый объём трафика: ГБ с двумя знаками, МБ — с одним."""
    if bytes_total >= 1024**3:
        return f"{bytes_total / 1024**3:.2f} ГБ"
    return f"{bytes_total / 1024**2:.1f} МБ"


def text_subscription_active(
    active_until: str | None,
    plan_name: str | None,
    device_count: int | None,
    *,
    remaining_days: int | None = None,
    monthly_traffic_bytes: int | None = None,
) -> str:
    lines = ["<b>✅ Ваша подписка активна!</b>"]
    if remaining_days is not None:
        lines.append(f"⏳ Осталось: {remaining_days} дн.")
    if device_count is not None:
        lines.append(f"📱 Устройств: {device_count}")
    if active_until:
        lines.append(f"📅 Действует до: {active_until}")
    if monthly_traffic_bytes is not None:
        lines.append(f"📊 Трафик за месяц: {_format_traffic(monthly_traffic_bytes)}")
    lines.extend(["", "Используйте кнопки меню для управления 📋"])
    return "\n".join(lines)


def text_subscription_expired() -> str:
    return "<b>❌ Ваша подписка истекла.</b>\n\nДля продления нажмите «🔑 Купить VPN»."


def text_no_subscription() -> str:
    return "У вас нет активной подписки.\n\nНажмите «🔑 Купить VPN» для оформления."


def _format_key_list_bounded(servers: tuple[VlessServerConfig, ...], *, budget: int = 3600) -> str:
    """format_key_list output, truncated to `budget` chars with a tail note if the full
    list of vless:// links would overflow Telegram's 4096-char message limit (Telegram
    rejects over-long messages -> "message is too long" 400). The subscription URL
    auto-imports ALL keys, so truncating the inline list is safe — the user still gets
    every key via the URL."""
    full = format_key_list(servers)
    if len(full) <= budget:
        return full
    note = "…остальные ключи — через ссылку подписки (она импортирует все ключи в приложение)."
    out: list[str] = []
    cur = 0
    for s in servers:
        block = f"{s.country_flag} {s.server_label}\n<code>{s.vless_link}</code>"
        addition = ("\n\n" if out else "") + block
        if cur + len(addition) + 2 + len(note) > budget:
            break
        out.append(block)
        cur += len(addition)
    return ("\n\n".join(out) + "\n\n" + note) if out else note


def text_my_keys(config: VlessUserConfig) -> str:
    lines = [
        "<b>🔐 Ваши настройки подключения:</b>\n",
        "📎 Ссылка для подписки (нажмите, чтобы скопировать):",
        f"<code>{config.subscription_url}</code>\n",
        "🔑 Ключи:",
    ]
    lines.append(_format_key_list_bounded(config.servers))
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
    # Назад ведёт в «Моя подписку» (контекстный возврат: единственный путь сюда — из подменю).
    rows.append([{"text": "↩️ Назад", "callback_data": CB_MY_SUB}])
    return _inline_kb(rows)


def text_my_keys_menu(*, subscription_url: str | None = None) -> str:
    lines = ["<b>🔐 Ваши ключи</b>\n"]
    if subscription_url:
        lines.append(f"🔗 Ссылка для подписки:\n<code>{subscription_url}</code>\n")
    lines.append("Выберите сервер или нажмите «Все ключи списком»:")
    return "\n".join(lines)


def text_single_server_key(server: VlessServerConfig) -> str:
    return f"🔑 {server.country_flag} {server.server_label}\n\n<code>{server.vless_link}</code>\n\n💡 Нажмите на ключ, чтобы скопировать."


def single_server_key_keyboard() -> dict[str, Any]:
    return _inline_kb([[{"text": "↩️ Назад", "callback_data": CB_MY_KEYS}]])


def text_all_keys_list(config: VlessUserConfig) -> str:
    lines = ["<b>🔐 Все ваши ключи:</b>\n"]
    lines.append(_format_key_list_bounded(config.servers))
    lines.append("💡 Нажмите на ключ, чтобы скопировать.")
    return "\n".join(lines)


def all_keys_list_keyboard() -> dict[str, Any]:
    return _inline_kb([[{"text": "↩️ Назад", "callback_data": CB_MY_KEYS}]])


def text_reissue_confirm() -> str:
    return (
        "<b>⚠️ Перевыпуск ключей</b>\n\n"
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
        "<b>🖥 Windows</b>\n\n"
        "Скачайте приложение:\n\n"
        "🔹 Karing\n"
        "https://github.com/KaringX/karing/releases\n\n"
        "🔹 Happ\n"
        "https://github.com/Happ-proxy/happ-desktop/releases\n\n"
        "Скачайте, установите и нажмите «Далее»."
    ),
    "android": (
        "<b>📱 Android</b>\n\n"
        "Скачайте приложение:\n\n"
        "🔹 Karing — кнопка «Скачать Karing (APK)» в шаге 1 либо\n"
        "https://github.com/KaringX/karing/releases\n\n"
        "🔹 Happ\n"
        "https://play.google.com/store/apps/details?id=com.happproxy\n\n"
        "🔹 v2rayTune\n"
        "https://play.google.com/store/apps/details?id=com.v2raytun.android&hl=ru\n\n"
        "Скачайте, установите и нажмите «Далее»."
    ),
    "ios": (
        "<b>📱 iOS</b>\n\n"
        "Скачайте приложение:\n\n"
        "🔹 Karing\n"
        "https://apps.apple.com/app/karing/id6472431552\n\n"
        "🔹 Happ\n"
        "https://apps.apple.com/app/happ-proxy-utility/id6504287215\n\n"
        "Скачайте, установите и нажмите «Далее»."
    ),
    "mac": (
        "<b>💻 macOS</b>\n\n"
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
    return "<b>📱 Выберите ваше устройство:</b>"


def _connect_device_rows() -> list[list[dict[str, Any]]]:
    return [
        [
            {"text": "🖥 Windows", "callback_data": CB_CONNECT_FAST_WIN},
            {"text": "🤖 Android", "callback_data": CB_CONNECT_FAST_ANDROID},
        ],
        [
            {"text": "📱 iPhone", "callback_data": CB_CONNECT_FAST_IOS},
            {"text": "💻 Mac", "callback_data": CB_CONNECT_FAST_MAC},
        ],
        [{"text": "📺 Телевизор", "callback_data": CB_CONNECT_TV}],
    ]


def connect_device_keyboard() -> dict[str, Any]:
    return _inline_kb([*_connect_device_rows(), [{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}]])


# ─── App chooser (Happ vs Karing) for ios/android/mac ───────────────
#
# Whitelist bypass ("обход белых списков") only works in Happ — the /sub/
# endpoint serves the RU-relay servers (surface='happ') solely to the Happ
# client; Karing gets the foreign fleet (surface='default'). So Happ is the
# recommended app. The Karing step-by-step (and its videos) stay available as
# the second button — see ios_step_*/android_step_*/mac_step_* below.


def _text_app_chooser(emoji: str, device_word: str) -> str:
    return (
        f"{emoji} <b>Выберите приложение для {device_word}</b>\n\n"
        "Рекомендуем <b>Happ</b> — в нём работает <b>обход белых списков</b> "
        "(подключение остаётся стабильным даже при жёстких блокировках провайдера).\n\n"
        "В Karing обход белых списков недоступен — он работает только на зарубежных серверах.\n\n"
        "Выберите приложение:"
    )


def text_ios_app_chooser() -> str:
    return _text_app_chooser("📱", "iPhone")


def text_android_app_chooser() -> str:
    return _text_app_chooser("🤖", "Android")


def text_mac_app_chooser() -> str:
    return _text_app_chooser("💻", "Mac")


def ios_app_chooser_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "🚀 Happ — рекомендуется", "callback_data": f"{CB_IOS_HAPP_STEP}1"}],
            [{"text": "📱 Karing", "callback_data": f"{CB_IOS_STEP}1"}],
            [{"text": "↩️ Назад", "callback_data": CB_CONNECT_DEVICE}],
        ]
    )


def android_app_chooser_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "🚀 Happ — рекомендуется", "callback_data": f"{CB_ANDROID_HAPP_STEP}1"}],
            [{"text": "📱 Karing", "callback_data": f"{CB_ANDROID_STEP}1"}],
            [{"text": "↩️ Назад", "callback_data": CB_CONNECT_DEVICE}],
        ]
    )


def mac_app_chooser_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "🚀 Happ — рекомендуется", "callback_data": f"{CB_MAC_HAPP_STEP}1"}],
            [{"text": "📱 Karing", "callback_data": f"{CB_MAC_STEP}1"}],
            [{"text": "↩️ Назад", "callback_data": CB_CONNECT_DEVICE}],
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
        "<b>⚙️ Подключение</b>\n\n"
        "Скопируйте ссылку ниже:\n"
        f"<code>{config.subscription_url}</code>\n\n"
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
    return "<b>🎉 Вы подключены!</b>\n\nНастройки защищённого соединения активны.\nЕсли возникнут вопросы — напишите в поддержку."


def connect_done_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "↩️ В главное меню", "callback_data": CB_MAIN_MENU}],
        ]
    )


# ─── iPhone (iOS) connection steps ─────────────────────────────────

_IOS_TOTAL_STEPS = 6

_IOS_KARING_APPSTORE_URL = "https://apps.apple.com/app/karing/id6472431552"
_IOS_CHANNEL_URL = "https://t.me/bravada_vpn"
_IOS_BYPASS_INSTRUCTIONS_URL = "https://t.me/bravada_instructions/2"
_SUPPORT_URL = "https://t.me/bravada_support"


def text_ios_step(step: int) -> str:
    return f"📱 <b>Шаг {step} из {_IOS_TOTAL_STEPS}</b>\n\nПосле каждого шага возвращайтесь в бота и нажимайте «Готово».\n\n"


def ios_step_1_text() -> str:
    return text_ios_step(1) + ("1. ⬇️ Нажмите «Скачать Karing» ниже или откройте App Store\n2. 📲 Установите приложение")


def ios_step_1_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "⬇️ Скачать Karing", "url": _IOS_KARING_APPSTORE_URL}],
            [
                {"text": "↩️ Назад", "callback_data": CB_CONNECT_IOS},
                {"text": "✅ Готово", "callback_data": f"{CB_IOS_STEP}2"},
            ],
        ]
    )


def ios_step_2_text() -> str:
    return text_ios_step(2) + (
        "1. 📂 Откройте Karing\n"
        "2. 👆 Нажмите «Accept and continue»\n"
        "3. 🇷🇺 Выберите русский язык\n"
        "4. ✅ «Дальше» → «Дальше» → «Готово»"
    )


def ios_step_2_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_IOS_STEP}1"},
                {"text": "✅ Готово", "callback_data": f"{CB_IOS_STEP}3"},
            ],
        ]
    )


def ios_step_3_text(subscription_url: str) -> str:
    return text_ios_step(3) + (
        "1. 🔑 Нажмите «Загрузить ключи» ниже — откроется Karing\n"
        "2. ✅ Нажмите галочку справа вверху\n\n"
        "<i>Ручное добавление:</i>\n"
        f"<code>{subscription_url}</code>"
    )


def ios_step_3_keyboard(subscription_url: str) -> dict[str, Any]:
    karing_url = subscription_url + ("&" if "?" in subscription_url else "?") + "open=karing"
    return _inline_kb(
        [
            [
                {
                    "text": "🔑 Загрузить ключи в Karing",
                    "url": karing_url,
                }
            ],
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_IOS_STEP}2"},
                {"text": "✅ Готово", "callback_data": f"{CB_IOS_STEP}4"},
            ],
        ]
    )


def ios_step_4_text() -> str:
    return text_ios_step(4) + (
        "🛡️ Включите VPN — нажмите кнопку со щитом в Karing\n\n⚠️ Следующий шаг — обход глушителей связи"
    )


def ios_step_4_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_IOS_STEP}3"},
                {"text": "✅ Готово", "callback_data": f"{CB_IOS_STEP}5"},
            ],
        ]
    )


def ios_step_5_text() -> str:
    return text_ios_step(5) + (
        "🔄 Karing автоматически выбирает сервер для обхода блокировок.\n\n"
        "⚠️ Серверы для обхода ограничены — 80 ГБ/мес.\n"
        "Используйте их только при необходимости."
    )


def ios_step_5_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_IOS_STEP}4"},
                {"text": "✅ Готово", "callback_data": f"{CB_IOS_STEP}6"},
            ],
        ]
    )


def ios_step_6_text() -> str:
    return (
        "📱 <b>Шаг 6 из 6</b>\n\n"
        "✅ VPN настроен!\n\n"
        "💡 Советы:\n"
        "• Оставьте Karing включённым для постоянной работы VPN\n"
        "• Если VPN перестал работать — нажмите «Начать заново»\n\n"
        "📢 Подпишитесь на канал, чтобы не пропустить обновления:\n"
        "@bravada_vpn"
    )


def ios_step_6_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "📢 Наш канал", "url": _IOS_CHANNEL_URL}],
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_IOS_STEP}5"},
                {"text": "✅ Готово", "callback_data": CB_IOS_DID_WORK},
            ],
        ]
    )


def ios_did_work_text() -> str:
    return "🌐 Заработал ли VPN?"


def ios_did_work_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "✅ Да, всё работает", "callback_data": CB_IOS_YES},
                {"text": "❌ Нет, есть проблемы", "callback_data": CB_IOS_NO},
            ],
            [{"text": "↩️ Назад", "callback_data": f"{CB_IOS_STEP}6"}],
        ]
    )


def ios_success_text() -> str:
    return "🎉 VPN подключён и работает! Если появятся вопросы — пишите в поддержку."


def ios_problem_text() -> str:
    return "😕 Что-то пошло не так.\nПопробуйте начать заново или напишите в поддержку — поможем!"


def ios_problem_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "🔄 Начать заново", "callback_data": CB_IOS_RETRY},
                {"text": "💬 Написать в поддержку", "url": _SUPPORT_URL},
            ],
            [{"text": "↩️ Назад", "callback_data": CB_IOS_DID_WORK}],
        ]
    )


# ─── Mac connection steps (identical flow to iPhone) ────────────────


def mac_step_1_text() -> str:
    return ios_step_1_text()


def mac_step_1_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "⬇️ Скачать Karing", "url": _IOS_KARING_APPSTORE_URL}],
            [
                {"text": "↩️ Назад", "callback_data": CB_CONNECT_MAC},
                {"text": "✅ Готово", "callback_data": f"{CB_MAC_STEP}2"},
            ],
        ]
    )


def mac_step_2_text() -> str:
    return ios_step_2_text().replace("📱", "💻", 1)


def mac_step_2_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_MAC_STEP}1"},
                {"text": "✅ Готово", "callback_data": f"{CB_MAC_STEP}3"},
            ],
        ]
    )


def mac_step_3_text(subscription_url: str) -> str:
    return ios_step_3_text(subscription_url).replace("📱", "💻", 1)


def mac_step_3_keyboard(subscription_url: str) -> dict[str, Any]:
    karing_url = subscription_url + ("&" if "?" in subscription_url else "?") + "open=karing"
    return _inline_kb(
        [
            [
                {
                    "text": "🔑 Загрузить ключи в Karing",
                    "url": karing_url,
                }
            ],
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_MAC_STEP}2"},
                {"text": "✅ Готово", "callback_data": f"{CB_MAC_STEP}4"},
            ],
        ]
    )


def mac_step_4_text() -> str:
    return ios_step_4_text().replace("📱", "💻", 1)


def mac_step_4_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_MAC_STEP}3"},
                {"text": "✅ Готово", "callback_data": f"{CB_MAC_STEP}5"},
            ],
        ]
    )


def mac_step_5_text() -> str:
    return ios_step_5_text().replace("📱", "💻", 1)


def mac_step_5_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_MAC_STEP}4"},
                {"text": "✅ Готово", "callback_data": f"{CB_MAC_STEP}6"},
            ],
        ]
    )


def mac_step_6_text() -> str:
    return ios_step_6_text().replace("📱", "💻", 1)


def mac_step_6_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "📢 Наш канал", "url": _IOS_CHANNEL_URL}],
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_MAC_STEP}5"},
                {"text": "✅ Готово", "callback_data": CB_MAC_DID_WORK},
            ],
        ]
    )


def mac_did_work_text() -> str:
    return ios_did_work_text()


def mac_did_work_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "✅ Да, всё работает", "callback_data": CB_MAC_YES},
                {"text": "❌ Нет, есть проблемы", "callback_data": CB_MAC_NO},
            ],
            [{"text": "↩️ Назад", "callback_data": f"{CB_MAC_STEP}6"}],
        ]
    )


def mac_success_text() -> str:
    return ios_success_text()


def mac_problem_text() -> str:
    return ios_problem_text()


def mac_problem_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "🔄 Начать заново", "callback_data": CB_MAC_RETRY},
                {"text": "💬 Написать в поддержку", "url": _SUPPORT_URL},
            ],
            [{"text": "↩️ Назад", "callback_data": CB_MAC_DID_WORK}],
        ]
    )


# ─── TV (Smart TV / Android TV) connection steps ────────────────────

_TV_TOTAL_STEPS = 5
_TV_HAPP_PLAYSTORE_URL = "https://play.google.com/store/apps/details?id=com.happproxy"


def tv_step_1_text() -> str:
    return (
        "<b>📺 Инструкция для Smart TV</b>\n\n"
        "⚠️ Важно! Только для телевизоров на Android TV!\n\n"
        f"📺 <b>Шаг 1 из {_TV_TOTAL_STEPS}</b>\n\n"
        "Пожалуйста, после каждого шага возвращайтесь обратно в бота и нажимайте «готово»!\n\n"
        "Скачиваем Happ Proxy Utility в Play Store"
    )


def tv_step_1_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "Скачать Happ Proxy Utility", "url": _TV_HAPP_PLAYSTORE_URL}],
            [
                {"text": "↩️ Назад", "callback_data": CB_CONNECT_DEVICE},
                {"text": "✅ Готово", "callback_data": f"{CB_TV_STEP}2"},
            ],
        ]
    )


def tv_step_2_text() -> str:
    return (
        f"📺 <b>Шаг 2 из {_TV_TOTAL_STEPS}</b>\n\n"
        "Пожалуйста, после каждого шага возвращайтесь обратно в бота и нажимайте «готово»!\n\n"
        "Открываем Happ и жмём на эту кнопку"
    )


def tv_step_2_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_TV_STEP}1"},
                {"text": "✅ Готово", "callback_data": f"{CB_TV_STEP}3"},
            ],
        ]
    )


def tv_step_3_text(subscription_url: str) -> str:
    return (
        f"📺 <b>Шаг 3 из {_TV_TOTAL_STEPS}</b>\n\n"
        "Пожалуйста, после каждого шага возвращайтесь обратно в бота и нажимайте «готово»!\n\n"
        "Скопируйте эту ссылку:\n"
        f"<code>{subscription_url}</code>\n\n"
        "После чего наведите камеру на этот QR и откройте его"
    )


def tv_step_3_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_TV_STEP}2"},
                {"text": "✅ Готово", "callback_data": f"{CB_TV_STEP}4"},
            ],
        ]
    )


def tv_step_4_text() -> str:
    return (
        f"📺 <b>Шаг 4 из {_TV_TOTAL_STEPS}</b>\n\n"
        "Пожалуйста, после каждого шага возвращайтесь обратно в бота и нажимайте «готово»!\n\n"
        "Вставляем скопированную ранее ссылку и нажимаем отправить данные\n\n"
        "На TV должен появиться список ключей"
    )


def tv_step_4_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_TV_STEP}3"},
                {"text": "✅ Готово", "callback_data": f"{CB_TV_STEP}5"},
            ],
        ]
    )


def tv_step_5_text() -> str:
    return (
        f"📺 <b>Шаг 5 из {_TV_TOTAL_STEPS}</b>\n\n"
        "Пожалуйста, после каждого шага возвращайтесь обратно в бота и нажимайте «готово»!\n\n"
        "Слева выбираем сервер\n"
        "Для ютуба отлично подойдет YouTube NoAds\n\n"
        "Включаем и выключаем кнопкой справа"
    )


def tv_step_5_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_TV_STEP}4"},
                {"text": "✅ Готово", "callback_data": CB_TV_DID_WORK},
            ],
        ]
    )


def tv_did_work_text() -> str:
    return ios_did_work_text()


def tv_did_work_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "✅ Да, всё работает", "callback_data": CB_TV_YES},
                {"text": "Нет, есть проблемы", "callback_data": CB_TV_NO},
            ],
            [{"text": "↩️ Назад", "callback_data": f"{CB_TV_STEP}5"}],
        ]
    )


def tv_success_text() -> str:
    return ios_success_text()


def tv_problem_text() -> str:
    return ios_problem_text()


def tv_problem_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "🔄 Начать заново", "callback_data": CB_TV_RETRY},
                {"text": "💬 Написать в поддержку", "url": _SUPPORT_URL},
            ],
            [{"text": "↩️ Назад", "callback_data": CB_TV_DID_WORK}],
        ]
    )


# ─── Android connection steps ──────────────────────────────────────

_ANDROID_TOTAL_STEPS = 6
_ANDROID_KARING_GITHUB_URL = "https://github.com/KaringX/karing/releases"
_ANDROID_HAPP_PLAYSTORE_URL = "https://play.google.com/store/apps/details?id=com.happproxy"
_ANDROID_V2RAYTUNE_URL = "https://play.google.com/store/apps/details?id=com.v2raytun.android&hl=ru"

# Where deploy publishes the always-latest APK manifest. The downloads dir is
# bind-mounted read-only into the backend container (see docker-compose.prod.yml).
_ANDROID_APK_MANIFEST_PATH = os.environ.get("ANDROID_APK_MANIFEST_PATH", "/var/www/downloads/latest-android.json")
_ANDROID_APK_URL_TTL_SECONDS = 60
_android_apk_url_cache: dict[str, Any] = {"url": None, "expires_at": 0.0}


def get_android_apk_url() -> str | None:
    """Resolve the always-latest self-hosted Karing APK URL, or ``None``.

    Reads the manifest written by deploy (``latest-android.json``) and returns an
    absolute URL (site base + the manifest's relative ``url``). Cached briefly so
    repeated renders don't re-read the file. Any error (missing file, bad JSON,
    missing fields) returns ``None`` — callers fall back to the GitHub link.
    """
    now = time.monotonic()
    cached = _android_apk_url_cache
    if cached["url"] and cached["expires_at"] > now:
        return cached["url"]  # type: ignore[return-value]

    url: str | None = None
    try:
        with open(_ANDROID_APK_MANIFEST_PATH, encoding="utf-8") as fh:
            manifest = json.load(fh)
        relative = str(manifest.get("url", "")).strip()
        if relative:
            url = f"{get_site_base_url().rstrip('/')}/{relative.lstrip('/')}"
    except (OSError, ValueError, TypeError):
        url = None

    cached["url"] = url
    cached["expires_at"] = now + _ANDROID_APK_URL_TTL_SECONDS
    return url


def _android_step_header(step: int) -> str:
    return f'🤖 <b>Шаг {step} из {_ANDROID_TOTAL_STEPS}</b>\n\nПосле каждого шага возвращайтесь в бота и нажимайте "Готово".\n\n'


def android_step_1_text() -> str:
    return _android_step_header(1) + (
        "1. ⬇️ Нажмите «Скачать Karing» ниже или откройте GitHub\n2. 📲 Установите приложение"
    )


def android_step_1_keyboard() -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    # Prefer the self-hosted latest APK (resolved from the deploy manifest); the
    # GitHub releases button stays as a fallback below it.
    apk_url = get_android_apk_url()
    if apk_url:
        rows.append([{"text": "⬇️ Скачать Karing (APK)", "url": apk_url}])
    rows.append([{"text": "Скачать Karing (GitHub)", "url": _ANDROID_KARING_GITHUB_URL}])
    rows.append(
        [
            {"text": "↩️ Назад", "callback_data": CB_CONNECT_ANDROID},
            {"text": "✅ Готово", "callback_data": f"{CB_ANDROID_STEP}2"},
        ]
    )
    return _inline_kb(rows)


def android_step_2_text() -> str:
    return _android_step_header(2) + (
        "1. 📂 Откройте Karing\n"
        "2. 👆 Нажмите «Accept and continue»\n"
        "3. 🇷🇺 Выберите русский язык\n"
        "4. ✅ «Дальше» → «Дальше» → «Готово»"
    )


def android_step_2_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_ANDROID_STEP}1"},
                {"text": "✅ Готово", "callback_data": f"{CB_ANDROID_STEP}3"},
            ],
        ]
    )


def android_step_3_text(subscription_url: str) -> str:
    return _android_step_header(3) + (
        '1. 🔑 Нажмите "Загрузить ключи" ниже — откроется Karing\n'
        "2. ✅ Нажмите галочку справа вверху\n\n"
        "Ручное добавление — скопируйте ссылку:\n"
        f"<code>{subscription_url}</code>"
    )


def android_step_3_keyboard(subscription_url: str) -> dict[str, Any]:
    sep = "&" if "?" in subscription_url else "?"
    karing_url = subscription_url + sep + "open=karing"
    return _inline_kb(
        [
            [
                {
                    "text": "🔑 Загрузить ключи в Karing",
                    "url": karing_url,
                }
            ],
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_ANDROID_STEP}2"},
                {"text": "✅ Готово", "callback_data": f"{CB_ANDROID_STEP}4"},
            ],
        ]
    )


def android_step_4_text() -> str:
    return _android_step_header(4) + (
        "🛡️ Включите VPN — нажмите кнопку со щитом в Karing\n\n⚠️ Следующий шаг — обход глушителей связи"
    )


def android_step_4_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_ANDROID_STEP}3"},
                {"text": "✅ Готово", "callback_data": f"{CB_ANDROID_STEP}5"},
            ],
        ]
    )


def android_step_5_text() -> str:
    return _android_step_header(5) + (
        "🔄 Karing автоматически выбирает сервер для обхода блокировок.\n\n"
        "⚠️ Серверы для обхода ограничены — 80 ГБ/мес.\n"
        "Используйте их только при необходимости."
    )


def android_step_5_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_ANDROID_STEP}4"},
                {"text": "✅ Готово", "callback_data": f"{CB_ANDROID_STEP}6"},
            ],
        ]
    )


def android_step_6_text() -> str:
    return (
        "🤖 <b>Шаг 6 из 6</b>\n\n"
        "✅ VPN настроен!\n\n"
        "💡 Советы:\n"
        "• Оставьте Karing включённым для постоянной работы VPN\n"
        "• Если VPN перестал работать — нажмите «Начать заново»\n\n"
        "📢 Подпишитесь на канал, чтобы не пропустить обновления:\n"
        "@bravada_vpn"
    )


def android_step_6_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "📢 Наш канал", "url": _IOS_CHANNEL_URL}],
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_ANDROID_STEP}5"},
                {"text": "✅ Готово", "callback_data": CB_ANDROID_DID_WORK},
            ],
        ]
    )


def android_did_work_text() -> str:
    return "🌐 Заработал ли VPN?"


def android_did_work_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "✅ Да, всё работает", "callback_data": CB_ANDROID_YES},
                {"text": "❌ Нет, есть проблемы", "callback_data": CB_ANDROID_NO},
            ],
            [{"text": "↩️ Назад", "callback_data": f"{CB_ANDROID_STEP}6"}],
        ]
    )


def android_success_text() -> str:
    return "🎉 VPN подключён и работает! Если появятся вопросы — пишите в поддержку."


def android_problem_text() -> str:
    return "😕 Что-то пошло не так.\nПопробуйте начать заново или напишите в поддержку — поможем!"


def android_problem_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "🔄 Начать заново", "callback_data": CB_ANDROID_RETRY},
                {"text": "💬 Написать в поддержку", "url": _SUPPORT_URL},
            ],
            [{"text": "↩️ Назад", "callback_data": CB_ANDROID_DID_WORK}],
        ]
    )


# ─── Happ connection steps (ios / android / mac) ────────────────────
#
# Happ is the recommended client: whitelist bypass works only here. Import is
# manual (copy subscription URL → paste into Happ's "add subscription"). There
# is no happ:// deep-link in web_api/subscription.py (only karing:// and
# v2raytun://), so we do not auto-open the app — Happ fetches the URL itself and
# its User-Agent triggers the surface='happ' RU-relay servers automatically.
# The flow ends at step 6 → success + main menu (reuses the platform's *_YES
# handler); the Karing "did it work?" loop is intentionally not duplicated here.

_HAPP_TOTAL_STEPS = 6
_IOS_HAPP_APPSTORE_URL = "https://apps.apple.com/app/happ-proxy-utility/id6504287215"
_ANDROID_HAPP_PLAYSTORE_URL = "https://play.google.com/store/apps/details?id=com.happproxy"
# v2rayTune rides inside the Happ branch as an alternative app (no separate fork):
# the instruction is shared. NOTE: bypass only works in Happ (surface='happ' is
# served by UA), so the steps stay Happ-oriented; v2rayTune is just an extra download.
# _ANDROID_V2RAYTUNE_URL is defined in the Android section above and reused here.
_IOS_V2RAYTUNE_URL = "https://apps.apple.com/app/v2raytun/id6476628951"


def _happ_step_header(emoji: str, step: int) -> str:
    return (
        f"{emoji} <b>Шаг {step} из {_HAPP_TOTAL_STEPS}</b>\n\n"
        "После каждого шага возвращайтесь в бота и нажимайте «Готово».\n\n"
    )


def _happ_step_1_text(emoji: str) -> str:
    return _happ_step_header(emoji, 1) + (
        "1. ⬇️ Нажмите «Скачать Happ» ниже (подойдёт и v2rayTune)\n2. 📲 Установите приложение"
    )


def _happ_step_2_text(emoji: str) -> str:
    return _happ_step_header(emoji, 2) + (
        "1. 📂 Откройте Happ\n"
        "2. ✅ Примите условия и выдайте разрешения\n"
        "3. 👆 Откройте раздел «Подписки» (или «Subscription»)"
    )


def _happ_step_3_text(emoji: str, subscription_url: str) -> str:
    return _happ_step_header(emoji, 3) + (
        "1. 🔑 Нажмите «Добавить подписку» (или «+») в Happ\n"
        "2. 📋 Вставьте скопированную ссылку:\n\n"
        f"<code>{subscription_url}</code>\n\n"
        "3. ✅ Подтвердите добавление\n\n"
        "Happ автоматически получит серверы с обходом белых списков."
    )


def _happ_step_4_text(emoji: str) -> str:
    return _happ_step_header(emoji, 4) + (
        "1. 🛡️ В Happ откройте добавленную подписку\n"
        "2. 🌍 Выберите сервер (обходной — для жёстких блокировок)\n"
        "3. ⚡ Нажмите кнопку подключения"
    )


def _happ_step_5_text(emoji: str) -> str:
    return _happ_step_header(emoji, 5) + (
        "✅ Обход белых списков активен — VPN работает даже при жёстких блокировках провайдера.\n\n"
        "💡 Если соединение не появилось — переподключитесь, выбрав другой сервер."
    )


def _happ_step_6_text(emoji: str) -> str:
    return _happ_step_header(emoji, 6) + (
        "✅ VPN настроен!\n\n"
        "💡 Советы:\n"
        "• Оставьте Happ включённым для постоянной работы VPN\n"
        "• Для обхода блокировок выбирайте обходной сервер\n\n"
        "📢 Подпишитесь на канал, чтобы не пропустить обновления:\n"
        "@bravada_vpn"
    )


def _happ_step_1_keyboard(happ_url: str, v2raytune_url: str, next_cb: str, back_cb: str) -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "⬇️ Скачать Happ", "url": happ_url}],
            [{"text": "⬇️ Скачать v2rayTune", "url": v2raytune_url}],
            [
                {"text": "↩️ Назад", "callback_data": back_cb},
                {"text": "✅ Готово", "callback_data": next_cb},
            ],
        ]
    )


def _happ_step_keyboard(back_cb: str, next_cb: str) -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": back_cb},
                {"text": "✅ Готово", "callback_data": next_cb},
            ],
        ]
    )


def _happ_step_6_keyboard(back_cb: str, done_cb: str) -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "📢 Наш канал", "url": _IOS_CHANNEL_URL}],
            [
                {"text": "↩️ Назад", "callback_data": back_cb},
                {"text": "✅ Готово", "callback_data": done_cb},
            ],
        ]
    )


# ─ iPhone (iOS) Happ wrappers ─


def ios_happ_step_1_text() -> str:
    return _happ_step_1_text("📱")


def ios_happ_step_1_keyboard() -> dict[str, Any]:
    return _happ_step_1_keyboard(_IOS_HAPP_APPSTORE_URL, _IOS_V2RAYTUNE_URL, f"{CB_IOS_HAPP_STEP}2", CB_CONNECT_IOS)


def ios_happ_step_2_text() -> str:
    return _happ_step_2_text("📱")


def ios_happ_step_2_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_IOS_HAPP_STEP}1", f"{CB_IOS_HAPP_STEP}3")


def ios_happ_step_3_text(subscription_url: str) -> str:
    return _happ_step_3_text("📱", subscription_url)


def ios_happ_step_3_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_IOS_HAPP_STEP}2", f"{CB_IOS_HAPP_STEP}4")


def ios_happ_step_4_text() -> str:
    return _happ_step_4_text("📱")


def ios_happ_step_4_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_IOS_HAPP_STEP}3", f"{CB_IOS_HAPP_STEP}5")


def ios_happ_step_5_text() -> str:
    return _happ_step_5_text("📱")


def ios_happ_step_5_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_IOS_HAPP_STEP}4", f"{CB_IOS_HAPP_STEP}6")


def ios_happ_step_6_text() -> str:
    return _happ_step_6_text("📱")


def ios_happ_step_6_keyboard() -> dict[str, Any]:
    return _happ_step_6_keyboard(f"{CB_IOS_HAPP_STEP}5", CB_IOS_YES)


# ─ Mac Happ wrappers (same App Store listing as iOS) ─


def mac_happ_step_1_text() -> str:
    return _happ_step_1_text("💻")


def mac_happ_step_1_keyboard() -> dict[str, Any]:
    return _happ_step_1_keyboard(_IOS_HAPP_APPSTORE_URL, _IOS_V2RAYTUNE_URL, f"{CB_MAC_HAPP_STEP}2", CB_CONNECT_MAC)


def mac_happ_step_2_text() -> str:
    return _happ_step_2_text("💻")


def mac_happ_step_2_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_MAC_HAPP_STEP}1", f"{CB_MAC_HAPP_STEP}3")


def mac_happ_step_3_text(subscription_url: str) -> str:
    return _happ_step_3_text("💻", subscription_url)


def mac_happ_step_3_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_MAC_HAPP_STEP}2", f"{CB_MAC_HAPP_STEP}4")


def mac_happ_step_4_text() -> str:
    return _happ_step_4_text("💻")


def mac_happ_step_4_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_MAC_HAPP_STEP}3", f"{CB_MAC_HAPP_STEP}5")


def mac_happ_step_5_text() -> str:
    return _happ_step_5_text("💻")


def mac_happ_step_5_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_MAC_HAPP_STEP}4", f"{CB_MAC_HAPP_STEP}6")


def mac_happ_step_6_text() -> str:
    return _happ_step_6_text("💻")


def mac_happ_step_6_keyboard() -> dict[str, Any]:
    return _happ_step_6_keyboard(f"{CB_MAC_HAPP_STEP}5", CB_MAC_YES)


# ─ Android Happ wrappers (Play Store) ─


def android_happ_step_1_text() -> str:
    return _happ_step_1_text("🤖")


def android_happ_step_1_keyboard() -> dict[str, Any]:
    return _happ_step_1_keyboard(
        _ANDROID_HAPP_PLAYSTORE_URL,
        _ANDROID_V2RAYTUNE_URL,
        f"{CB_ANDROID_HAPP_STEP}2",
        CB_CONNECT_ANDROID,
    )


def android_happ_step_2_text() -> str:
    return _happ_step_2_text("🤖")


def android_happ_step_2_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_ANDROID_HAPP_STEP}1", f"{CB_ANDROID_HAPP_STEP}3")


def android_happ_step_3_text(subscription_url: str) -> str:
    return _happ_step_3_text("🤖", subscription_url)


def android_happ_step_3_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_ANDROID_HAPP_STEP}2", f"{CB_ANDROID_HAPP_STEP}4")


def android_happ_step_4_text() -> str:
    return _happ_step_4_text("🤖")


def android_happ_step_4_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_ANDROID_HAPP_STEP}3", f"{CB_ANDROID_HAPP_STEP}5")


def android_happ_step_5_text() -> str:
    return _happ_step_5_text("🤖")


def android_happ_step_5_keyboard() -> dict[str, Any]:
    return _happ_step_keyboard(f"{CB_ANDROID_HAPP_STEP}4", f"{CB_ANDROID_HAPP_STEP}6")


def android_happ_step_6_text() -> str:
    return _happ_step_6_text("🤖")


def android_happ_step_6_keyboard() -> dict[str, Any]:
    return _happ_step_6_keyboard(f"{CB_ANDROID_HAPP_STEP}5", CB_ANDROID_YES)


# ─── Windows connection steps ──────────────────────────────────────

_WIN_KARING_GITHUB_URL = "https://github.com/KaringX/karing/releases"


def win_step_1_text() -> str:
    return (
        "🖥 <b>Шаг 1 из 6</b>\n\n"
        "После каждого шага возвращайтесь в бота и нажимайте «Готово».\n\n"
        "1. ⬇️ Скачайте Karing — нажмите кнопку ниже или перейдите на GitHub\n"
        "2. 📲 Установите приложение"
    )


def win_step_1_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "Скачать Karing (GitHub)", "url": _WIN_KARING_GITHUB_URL}],
            [
                {"text": "↩️ Назад", "callback_data": CB_CONNECT_DEVICE},
                {"text": "✅ Готово", "callback_data": f"{CB_WIN_STEP}2"},
            ],
        ]
    )


def win_step_2_text() -> str:
    return ios_step_2_text().replace("📱", "🖥", 1)


def win_step_2_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_WIN_STEP}1"},
                {"text": "✅ Готово", "callback_data": f"{CB_WIN_STEP}3"},
            ],
        ]
    )


def win_step_3_text(subscription_url: str) -> str:
    return ios_step_3_text(subscription_url).replace("📱", "🖥", 1)


def win_step_3_keyboard(subscription_url: str) -> dict[str, Any]:
    karing_url = subscription_url + ("&" if "?" in subscription_url else "?") + "open=karing"
    return _inline_kb(
        [
            [
                {
                    "text": "🔑 Загрузить ключи в Karing",
                    "url": karing_url,
                }
            ],
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_WIN_STEP}2"},
                {"text": "✅ Готово", "callback_data": f"{CB_WIN_STEP}4"},
            ],
        ]
    )


def win_step_4_text() -> str:
    return ios_step_4_text().replace("📱", "🖥", 1)


def win_step_4_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_WIN_STEP}3"},
                {"text": "✅ Готово", "callback_data": f"{CB_WIN_STEP}5"},
            ],
        ]
    )


def win_step_5_text() -> str:
    return ios_step_5_text().replace("📱", "🖥", 1)


def win_step_5_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_WIN_STEP}4"},
                {"text": "✅ Готово", "callback_data": f"{CB_WIN_STEP}6"},
            ],
        ]
    )


def win_step_6_text() -> str:
    return ios_step_6_text().replace("📱", "🖥", 1)


def win_step_6_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "📢 Наш канал", "url": _IOS_CHANNEL_URL}],
            [
                {"text": "↩️ Назад", "callback_data": f"{CB_WIN_STEP}5"},
                {"text": "✅ Готово", "callback_data": CB_WIN_DID_WORK},
            ],
        ]
    )


def win_did_work_text() -> str:
    return ios_did_work_text()


def win_did_work_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "✅ Да, всё работает", "callback_data": CB_WIN_YES},
                {"text": "Нет, есть проблемы", "callback_data": CB_WIN_NO},
            ],
            [{"text": "↩️ Назад", "callback_data": f"{CB_WIN_STEP}6"}],
        ]
    )


def win_success_text() -> str:
    return ios_success_text()


def win_problem_text() -> str:
    return ios_problem_text()


def win_problem_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [
                {"text": "🔄 Начать заново", "callback_data": CB_WIN_RETRY},
                {"text": "💬 Написать в поддержку", "url": _SUPPORT_URL},
            ],
            [{"text": "↩️ Назад", "callback_data": CB_WIN_DID_WORK}],
        ]
    )


_FAST_PLATFORM_BY_CALLBACK = {
    CB_CONNECT_FAST_IOS: "ios",
    CB_CONNECT_FAST_ANDROID: "android",
    CB_CONNECT_FAST_MAC: "mac",
    CB_CONNECT_FAST_WIN: "win",
}
_FAST_CALLBACK_BY_PLATFORM = {platform: callback for callback, platform in _FAST_PLATFORM_BY_CALLBACK.items()}
_FAST_PLATFORM_TITLE = {
    "ios": "📱 iPhone",
    "android": "🤖 Android",
    "mac": "💻 Mac",
    "win": "🖥 Windows",
}
_FAST_APP_DOWNLOAD = {
    "ios": ("⬇️ Установить Happ", _IOS_HAPP_APPSTORE_URL),
    "android": ("⬇️ Установить Happ", _ANDROID_HAPP_PLAYSTORE_URL),
    "mac": ("⬇️ Установить Happ", _IOS_HAPP_APPSTORE_URL),
    "win": ("⬇️ Установить Karing", _WIN_KARING_GITHUB_URL),
}
_FAST_DETAILS_CALLBACK = {
    "ios": CB_CONNECT_IOS,
    "android": CB_CONNECT_ANDROID,
    "mac": CB_CONNECT_MAC,
    "win": CB_CONNECT_WIN,
}
_FAST_SUCCESS_CALLBACK = {
    "ios": CB_IOS_YES,
    "android": CB_ANDROID_YES,
    "mac": CB_MAC_YES,
    "win": CB_WIN_YES,
}
_FAST_PROBLEM_CALLBACK = {platform: f"{CB_CONNECT_FAST_PROBLEM}{platform}" for platform in _FAST_CALLBACK_BY_PLATFORM}


def connect_fast_platform(callback_data: str) -> str | None:
    return _FAST_PLATFORM_BY_CALLBACK.get(callback_data)


def connect_fast_problem_platform(callback_data: str) -> str | None:
    if not callback_data.startswith(CB_CONNECT_FAST_PROBLEM):
        return None
    platform = callback_data[len(CB_CONNECT_FAST_PROBLEM) :]
    return platform if platform in _FAST_CALLBACK_BY_PLATFORM else None


def text_connect_fastpath_problem(platform: str) -> str:
    if platform == "win":
        fallback = "откройте пошаговую инструкцию Karing"
    else:
        fallback = "выберите другое приложение"
    return f"😕 Подключение не получилось.\n\nПовторите короткий способ, {fallback} или напишите в поддержку — поможем."


def connect_fastpath_problem_keyboard(platform: str) -> dict[str, Any]:
    fast_callback = _FAST_CALLBACK_BY_PLATFORM.get(platform)
    details_callback = _FAST_DETAILS_CALLBACK.get(platform)
    if fast_callback is None or details_callback is None:
        return back_only_keyboard(CB_CONNECT_DEVICE)

    rows: list[list[dict[str, Any]]] = [
        [{"text": "🔄 Повторить подключение", "callback_data": fast_callback}],
    ]
    if platform == "win":
        rows.append([{"text": "🧭 Пошаговая инструкция Karing", "callback_data": details_callback}])
    else:
        rows.append([{"text": "🔁 Выбрать другое приложение", "callback_data": details_callback}])
    rows.extend(
        [
            [{"text": "💬 Написать в поддержку", "url": _SUPPORT_URL}],
            [{"text": "↩️ К выбору устройства", "callback_data": CB_CONNECT_DEVICE}],
        ]
    )
    return _inline_kb(rows)


_CONNECTION_FEEDBACK_LABELS = (
    ("install_failed", "📲 Не установилось приложение"),
    ("import_failed", "🔗 Не добавилась подписка"),
    ("connect_failed", "🔌 VPN не подключается"),
    ("traffic_failed", "🌐 Подключилось, но интернет не работает"),
    ("too_hard", "🧩 Слишком сложно"),
    ("other", "💬 Другая причина"),
)
_NONPAYMENT_FEEDBACK_LABELS = (
    ("too_expensive", "💰 Дорого"),
    ("did_not_work", "🔌 VPN не заработал"),
    ("too_hard", "🧩 Сложно настроить"),
    ("no_need", "🤷 Пока не нужен"),
    ("payment_issue", "💳 Не получилось оплатить"),
    ("other", "💬 Другая причина"),
)


def text_connection_feedback_prompt() -> str:
    return "😕 <b>Что именно не получилось?</b>\n\nОдин ответ поможет нам исправить подключение. Его можно пропустить."


def connection_feedback_keyboard(platform: str, connection_route: str) -> dict[str, Any]:
    if platform not in _FAST_CALLBACK_BY_PLATFORM and platform != "tv":
        return back_only_keyboard(CB_CONNECT_DEVICE)
    route_token = {"direct": "d", "guided": "g"}.get(connection_route)
    if route_token is None:
        return back_only_keyboard(CB_CONNECT_DEVICE)
    rows = [
        [
            {
                "text": label,
                "callback_data": f"{CB_TRIAL_FEEDBACK_ANSWER}p:{route_token}:{platform}:{reason}",
            }
        ]
        for reason, label in _CONNECTION_FEEDBACK_LABELS
    ]
    rows.append(
        [
            {
                "text": "Пропустить и решить проблему",
                "callback_data": f"{CB_TRIAL_FEEDBACK_ANSWER}p:{route_token}:{platform}:skip",
            }
        ]
    )
    rows.append([{"text": "💬 Сразу в поддержку", "url": _SUPPORT_URL}])
    return _inline_kb(rows)


def text_trial_nonpayment_feedback_prompt() -> str:
    return "👋 <b>Что остановило от подписки?</b>\n\nВыберите одну причину — ответ займёт пару секунд."


def trial_nonpayment_feedback_keyboard() -> dict[str, Any]:
    rows = [
        [{"text": label, "callback_data": f"{CB_TRIAL_FEEDBACK_ANSWER}n:{reason}"}]
        for reason, label in _NONPAYMENT_FEEDBACK_LABELS
    ]
    rows.append([{"text": "Пропустить и вернуться к тарифам", "callback_data": CB_BUY_VPN}])
    return _inline_kb(rows)


def parse_trial_feedback_answer(callback_data: str) -> tuple[str, str, str, str] | None:
    """Возвращает type, reason, platform, route только для закрытого callback enum."""
    if not callback_data.startswith(CB_TRIAL_FEEDBACK_ANSWER):
        return None
    parts = callback_data[len(CB_TRIAL_FEEDBACK_ANSWER) :].split(":")
    if len(parts) == 4 and parts[0] == "p":
        route = {"d": "direct", "g": "guided"}.get(parts[1])
        platform, reason = parts[2], parts[3]
        if (
            route is not None
            and platform in {*_FAST_CALLBACK_BY_PLATFORM, "tv"}
            and (reason in CONNECTION_PROBLEM_REASONS or reason == "skip")
        ):
            return "connection_problem", reason, platform, route
    if len(parts) == 2 and parts[0] == "n" and parts[1] in TRIAL_NONPAYMENT_REASONS:
        return "trial_nonpayment", parts[1], "", ""
    return None


def text_trial_feedback_thanks() -> str:
    return "🙏 <b>Спасибо за ответ</b>\n\nМы учтём его при улучшении Bravada. Настройки сохранятся, если захотите вернуться."


def trial_feedback_thanks_keyboard() -> dict[str, Any]:
    return _inline_kb(
        [
            [{"text": "⚡ Вернуть VPN", "callback_data": CB_BUY_VPN, "style": "success"}],
            [{"text": "❓ Нужна помощь", "callback_data": CB_HELP}],
            [{"text": "↩️ В главное меню", "callback_data": CB_MAIN_MENU}],
        ]
    )


def text_connect_fastpath(platform: str, subscription_url: str) -> str:
    title = _FAST_PLATFORM_TITLE.get(platform, "📱 Устройство")
    safe_url = escape(subscription_url)
    if platform == "win":
        steps = (
            "1. Установите <b>Karing</b>.\n"
            "2. Нажмите «Импортировать в Karing» — ключи добавятся автоматически.\n"
            "3. В Karing выберите сервер и включите VPN."
        )
    else:
        steps = (
            "1. Установите <b>Happ</b>.\n"
            "2. Скопируйте ссылку, затем в Happ нажмите «+» → «Подписка» и вставьте её.\n"
            "3. Выберите сервер и включите VPN."
        )
    return (
        f"{title} <b>Подключение за минуту</b>\n\n"
        f"{steps}\n\n"
        "<b>Ваша ссылка:</b>\n"
        f"<code>{safe_url}</code>\n\n"
        "Если короткий способ не подойдёт, откройте подробную инструкцию."
    )


def connection_check_url(subscription_url: str) -> str | None:
    """URL страницы проверки подключения: /sub/<token> → /check/<token>.

    Возвращает None для посторонних форматов — кнопка просто не добавляется.
    """
    marker = "/sub/"
    at = subscription_url.rfind(marker)
    if at == -1:
        return None
    base = subscription_url[:at]
    token = subscription_url[at + len(marker) :]
    if not base.startswith(("https://", "http://")) or not 8 <= len(token) <= 128 or "/" in token:
        return None
    return f"{base}/check/{token}"


def connect_fastpath_keyboard(platform: str, subscription_url: str) -> dict[str, Any]:
    download = _FAST_APP_DOWNLOAD.get(platform)
    details_callback = _FAST_DETAILS_CALLBACK.get(platform, CB_CONNECT_DEVICE)
    success_callback = _FAST_SUCCESS_CALLBACK.get(platform, CB_CONNECT_DONE)
    problem_callback = _FAST_PROBLEM_CALLBACK.get(platform, CB_CONNECT_DEVICE)
    rows: list[list[dict[str, Any]]] = []
    if download is not None:
        rows.append([{"text": download[0], "url": download[1]}])
    if platform == "win" and subscription_url.startswith(("https://", "http://")):
        separator = "&" if "?" in subscription_url else "?"
        rows.append([{"text": "🔑 Импортировать в Karing", "url": f"{subscription_url}{separator}open=karing"}])
    if 1 <= len(subscription_url) <= 256:
        rows.append([{"text": "📋 Скопировать ссылку", "copy_text": {"text": subscription_url}}])
    # Автоматическая проверка подключения (web): кнопка только при включённом флаге
    # и валидном URL — ручные кнопки ниже остаются без изменений.
    check_url = connection_check_url(subscription_url) if connection_check_enabled() else None
    if check_url is not None:
        rows.append([{"text": "🧪 Проверить подключение", "url": check_url}])
    rows.extend(
        [
            [
                {"text": "✅ VPN работает", "callback_data": success_callback},
                {"text": "❓ Не получилось", "callback_data": problem_callback},
            ],
            [{"text": "🧭 Подробная инструкция", "callback_data": details_callback}],
            [{"text": "↩️ К выбору устройства", "callback_data": CB_CONNECT_DEVICE}],
        ]
    )
    return _inline_kb(rows)


def text_subscription_url(config: VlessUserConfig) -> str:
    return (
        "<b>📎 Ссылка для приложений:</b>\n\n"
        f"<code>{config.subscription_url}</code>\n\n"
        "💡 Скопируйте эту ссылку и вставьте в приложение:\n"
        "Karing, v2rayTune, Happ, v2rayNG и др.\n"
        "Все ключи подтянутся автоматически."
    )


def text_keys_not_available() -> str:
    return (
        "<b>⚠️ Ключи недоступны.</b>\n\nВозможно, у вас нет активной подписки.\nНажмите «🔑 Купить VPN» для оформления."
    )


def text_referral_program(info: ReferralInfo) -> str:
    legacy_note = (
        f"\nРанее начислено по прежним условиям: <b>{info.bonus_days_awarded} дней</b>\n"
        if info.bonus_days_awarded > 0
        else ""
    )
    return (
        "<b>👥 Реферальная программа</b>\n\n"
        "🎁 <b>Бонус получают оба</b>\n\n"
        "Друг получит <b>+3 дня</b> к первой подписке от 1 месяца.\n"
        "Вы получите <b>до 20%</b> с его оплат и до 2% — с оплат его приглашённых.\n"
        "Бонусы поступают на баланс — ими можно оплатить VPN или вывести на криптокошелёк*.\n\n"
        "<b>💰 Ваш доход от рефералки</b>\n"
        f"Доступно на балансе: <b>{info.balance_rubles:.2f} ₽</b>\n"
        f"Всего заработано: <b>{info.total_earned_rubles:.2f} ₽</b>\n"
        "\n"
        f"👥 Приглашено друзей: <b>{info.l1_invited}</b>\n"
        f"{legacy_note}\n"
        "Выберите, как пригласить друга:"
    )


def referral_program_keyboard(info: ReferralInfo) -> dict[str, Any]:
    """Кнопки персонального приглашения внутри и за пределами Telegram."""
    rows: list[list[dict[str, str]]] = []
    if info.referral_link:
        share_url = "https://t.me/share/url?" + urlencode(
            {
                "url": info.referral_link,
                "text": "Попробуй Bravada VPN — безопасный доступ к интернету.",
            },
            quote_via=quote,
        )
        rows.append([{"text": "📨 Пригласить друга в Telegram", "url": share_url}])
    if info.web_referral_link:
        rows.append(
            [
                {
                    "text": "🌐 Пригласить друга без Telegram",
                    "copy_text": {"text": info.web_referral_link},
                }
            ]
        )
    rows.append([{"text": "↩️ Назад", "callback_data": CB_MAIN_MENU}])
    return _inline_kb(rows)


def text_balance(info: ReferralBalanceInfo) -> str:
    return f"<b>💰 Ваш баланс:</b> {info.balance_rubles:.2f} ₽"


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
    lines = ["<b>⚙️ Настройки подписки</b>\n"]
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
    return "<b>📶 Конфиг для роутера</b>\n\n🔜 Эта функция скоро будет доступна!\nСледите за обновлениями."


FAQ_ITEMS: tuple[tuple[str, str], ...] = (
    (
        "Что такое услуга защищённого сетевого доступа?",
        "Это персональный защищённый сетевой туннель, который обеспечивает шифрование вашего интернет-соединения и защиту трафика при использовании публичных и ненадёжных сетей.",
    ),
    (
        "Как подключиться к сервису?",
        "Оформите подписку через Telegram-бота или на сайте. После оплаты доступ активируется автоматически, а настройки подключения будут доступны в личном кабинете и Telegram-боте.",
    ),
    (
        "Какие устройства поддерживаются?",
        f"Сервис работает на всех основных платформах: iOS, Android, Windows, macOS и Linux. Количество устройств зависит от тарифа — до {MAX_DEVICE_COUNT}.",
    ),
    (
        "Как происходит оплата?",
        "Оплата производится на сайте через платёжный сервис. Мы принимаем банковские карты, СБП и другие способы оплаты. После подтверждения оплаты доступ активируется автоматически.",
    ),
    (
        "Можно ли вернуть деньги?",
        "Да, вы можете запросить возврат в течение 7 дней с момента оплаты. Подробные условия описаны на странице «Условия возврата».",
    ),
    (
        "Как связаться с поддержкой?",
        "Вы можете написать нам в Telegram или на электронную почту. Контакты указаны в соответствующем разделе сайта.",
    ),
    (
        "Можно ли попробовать сервис бесплатно?",
        "Да. Доступен бесплатный пробный период на 3 дня — без привязки карты. Оформить пробный можно через Telegram-бота.",
    ),
    (
        "Что произойдёт после окончания пробного периода?",
        "По окончании пробного периода доступ прекращается. Подписка не включается автоматически — вы сами выбираете тариф и оплачиваете, когда вам удобно.",
    ),
    (
        "Можно ли использовать несколько устройств одновременно?",
        "Да, в рамках выбранного тарифа можно подключить несколько устройств. Точное количество зависит от тарифа.",
    ),
    (
        "Происходит ли автопродление подписки?",
        "Нет, автопродления нет. Подписка действует до конца оплаченного срока. Продлить можно вручную в любой момент до или после окончания срока.",
    ),
)


def text_help() -> str:
    sections = ["<b>❓ Помощь</b>"]
    for question, answer in FAQ_ITEMS:
        sections.append(f"<b>{question}</b>\n{answer}")
    sections.append('<b>Остались вопросы?</b>\n💬 <a href="https://t.me/bravada_support">Написать в поддержку</a>')
    return "\n\n".join(sections)


def rich_help_message() -> dict[str, Any]:
    """Собрать нативные Telegram details-блоки для аккордеона «Помощь»."""

    blocks: list[dict[str, Any]] = [
        {"type": "heading", "text": "❓ Помощь", "size": 2},
    ]
    blocks.extend(
        {
            "type": "details",
            "summary": question,
            "blocks": [{"type": "paragraph", "text": answer}],
        }
        for question, answer in FAQ_ITEMS
    )
    blocks.append(
        {
            "type": "paragraph",
            "text": [
                {"type": "bold", "text": "Остались вопросы?"},
                "\n💬 ",
                {
                    "type": "url",
                    "text": "Написать в поддержку",
                    "url": "https://t.me/bravada_support",
                },
            ],
        }
    )
    return {"blocks": blocks}


def text_fulfillment_success(active_until: str | None) -> str:
    lines = ["<b>✅ Оплата получена!</b>", "", "Ваша подписка активирована."]
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
    lines = ["<b>✅ Подписка оплачена с реферального баланса!</b>", "", "Ваша подписка активирована."]
    if active_until:
        lines.extend(["", f"📅 Действует до: {active_until}"])
    lines.extend(["", "🔐 Нажмите «Мои ключи» для получения VPN-ключей."])
    return "\n".join(lines)


def text_balance_insufficient() -> str:
    return "❌ Недостаточно средств на балансе.\n\nПриглашайте друзей по реферальной ссылке, чтобы пополнить баланс."


def text_balance_payment_in_progress() -> str:
    """Shown when a second balance-payment click arrives within the debounce window
    (accidental double-click). The first click is still processing/has processed —
    no second debit must happen."""
    return "⏳ Платёж ещё обрабатывается.\n\nОткройте «Мои подписки», чтобы увидеть результат."


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
    # Mirror confirm_pay_keyboard: the card ("💳 Оплатить") button is ALWAYS shown
    # (real YooKassa payment), and the balance button is added ONLY when the user
    # has enough referral balance. Previously the card path was a dead-end stub
    # ("payment unavailable") shown when balance was insufficient — see
    # _process_add_device_yookassa for the real payment now backing this button.
    rows: list[list[dict[str, str]]] = [
        [{"text": "💳 Оплатить", "callback_data": f"add_dev_pay:{new_count}"}],
    ]
    if _cryptobot_enabled():
        rows.append([{"text": "₮ Оплатить криптой", "callback_data": f"add_dev_pay_crypto:{new_count}"}])
    if balance_kopecks >= cost_kopecks and cost_kopecks > 0:
        rows.append(
            [
                {
                    "text": f"💰 С баланса ({cost_kopecks // 100} ₽)",
                    "callback_data": f"{CB_ADD_DEV_BALANCE}{new_count}",
                }
            ]
        )
    rows.append([{"text": "↩️ Назад", "callback_data": CB_ADD_DEVICE}])
    return _inline_kb(rows)


def text_add_device_intro(current_count: int) -> str:
    lines = [
        "<b>📱 Добавление устройств</b>",
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
        "<b>📱 Подтверждение</b>",
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
        f"<b>📱 Снижение устройств</b>\n\n"
        f"Текущее: {current}\n"
        f"Новое: {new_count}\n\n"
        f"Новое количество будет учтено при следующем продлении."
    )


def text_remove_device_success(new_count: int) -> str:
    return f"✅ Готово! Теперь у вас {new_count} устройств."


# ─── Email linking ──────────────────────────────────────────────────


def link_email_keyboard() -> dict[str, Any]:
    # Назад ведёт в «Моя подписку» (контекстный возврат: единственный путь сюда — из подменю).
    return _inline_kb([[{"text": "↩️ Назад", "callback_data": CB_MY_SUB}]])


def link_email_code_keyboard() -> dict[str, Any]:
    # Экран кода предшествует вводу email: Назад возвращает к экрану ввода (откуда пришли).
    return _inline_kb(
        [
            [{"text": "📧 Отправить код повторно", "callback_data": CB_RESEND_EMAIL_CODE}],
            [{"text": "↩️ Назад", "callback_data": CB_LINK_EMAIL}],
        ]
    )


def text_link_email_intro(email: str | None = None) -> str:
    if email:
        return (
            "<b>📧 Привязка email</b>\n\n"
            f"Текущий email: {email}\n\n"
            "Если хотите заменить, введите новый email.\n"
            "Для подтверждения на почту будет отправлен код."
        )
    return "<b>📧 Привязка email</b>\n\nВведите ваш email-адрес.\nНа него будет отправлен код подтверждения."


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
        "<b>🎁 Попробуйте VPN бесплатно!</b>\n\n"
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
        "<b>🎉 Пробный период активирован!</b>\n",
        "⏳ Действует 3 дня.\n",
        "📎 Ссылка для подписки (нажмите, чтобы скопировать):",
        f"<code>{config.subscription_url}</code>\n",
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


def text_trial_connect_fastpath(*, days: int = 3) -> str:
    """Текст успеха активации триала; days — фактическая длительность
    (эксперимент trial_duration_v1)."""
    label = _ru_days_plural(days)
    return (
        "✅ <b>Пробный период активирован!</b>\n\n"
        f"Ключи уже готовы. Полные {label} начнутся после первого подключения.\n"
        f"Подключите VPN в течение {label}, чтобы запустить отсчёт.\n"
        "Выберите устройство — основная настройка займёт около минуты."
    )


def _ru_days_plural(count: int) -> str:
    """«N день/дня/дней» — 1 день, 2 дня, 5 дней, 11 дней, 21 день…"""
    if count % 10 == 1 and count % 100 != 11:
        form = "день"
    elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        form = "дня"
    else:
        form = "дней"
    return f"{count} {form}"


def trial_connect_fastpath_keyboard(*, channel_suggest: bool = False) -> dict[str, Any]:
    """Клавиатура после активации триала; при channel_suggest (soft-вариант A/B
    гейта) добавляем необязательное предложение подписки на канал."""
    rows = [*_connect_device_rows()]
    if channel_suggest:
        channel_username = channel_username_from_env()
        rows.append([{"text": "📣 Подписаться на канал", "url": f"https://t.me/{channel_username}"}])
    rows.append([{"text": "🏠 Главное меню", "callback_data": CB_MAIN_MENU}])
    return _inline_kb(rows)


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


# ─── Promocode flow (ввод текстом по образцу «свой тариф») ──────────────


def text_promo_prompt() -> str:
    return (
        "🎟️ Промокод\n\n"
        "Введите промокод одним сообщением — скидка применится автоматически при следующей "
        "оплате подписки или устройств и будет действовать 24 часа."
    )


def promo_prompt_keyboard(*, back_callback: str = CB_MAIN_MENU) -> dict[str, Any]:
    return _inline_kb([[{"text": "↩️ Назад", "callback_data": back_callback}]])


def text_promo_code_invalid(user_input: str) -> str:
    # Экранируем ввод: сообщение уходит в HTML parse_mode, а сюда попадает код,
    # не прошедший валидацию (т.е. возможно содержащий <, &, и т. п.).
    from html import escape

    return (
        f"❌ «{escape(user_input)}» — некорректный промокод.\n\n"
        "Код — от 3 до 32 символов (только латиница и цифры). Попробуйте ещё раз."
    )


def text_promo_not_found() -> str:
    return "❌ Промокод не найден или больше неактивен.\n\nПроверьте написание или срок действия кода."


def text_promo_ineligible() -> str:
    # Аудитория (new/existing) — общая формулировка, детали намеренно скрыты.
    return "❌ Этот промокод недоступен для вашей учётной записи."


def text_promo_already_used() -> str:
    # Per-user лимит исчерпан: код с режимом «единожды» уже погашен этим пользователем.
    return "❌ Вы уже активировали этот промокод ранее.\n\nЭтот код можно использовать только один раз."


def text_promo_rate_limited() -> str:
    return "⏳ Слишком много попыток ввода. Попробуйте ввести промокод чуть позже."


def promo_offer_preview(promo: PromoCode) -> str:
    """Человекочитаемое описание скидки без привязки к тарифу (тариф ещё не выбран)."""
    if promo.discounts_by_plan:
        return "Скидка по тарифам — выберите тариф, цену пересчитаем со скидкой."
    if promo.discount_kind == PromoDiscountKind.PERCENT:
        return f"Скидка: {promo.discount_value}% от стоимости."
    return f"Скидка: {promo.discount_value} ₽ от стоимости."


def promo_badge_label(promo: PromoCode) -> str:
    """Короткая подпись скидки для бейджей/кнопок: «−15%», «−200 ₽» или «по тарифам»."""
    if promo.discounts_by_plan:
        return "по тарифам"
    if promo.discount_kind == PromoDiscountKind.PERCENT:
        return f"−{promo.discount_value}%"
    return f"−{promo.discount_value} ₽"


def promo_menu_badge(code: str, label: str, remaining_hours: int) -> str:
    """Строка-бейдж закреплённого промокода для главного меню / статуса подписки."""
    return f"\n\n🎁 Промокод {code}: {label} (ещё {remaining_hours} ч)"


def text_promo_applied_in_order(code: str, label: str) -> str:
    """Короткая подтверждайка при успешном вводе промокода внутри оплаты (над сводкой)."""
    return f"✅ Промокод {code} применён ({label}) — скидка учтена в заказе ниже."


def text_promo_applied(code: str, *, offer_text: str) -> str:
    return (
        f"✅ Промокод {code} применён!\n\n{offer_text}\n\nСкидка действует 24 часа и будет учтена при следующей оплате."
    )
