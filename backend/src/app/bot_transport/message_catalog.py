"""Pure slice-1 message catalog: TelegramOutboundPlan → neutral rendered text (no SDK)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.bot_transport.outbound import (
    OutboundKeyboardMarker,
    OutboundMessageKey,
    TelegramOutboundPlan,
)
from app.bot_transport.storefront_config import (
    build_checkout_url_with_reference,
    load_checkout_reference_secret,
    load_storefront_public_config,
)
from app.bot_transport.support_catalog import build_support_contact_text, build_support_menu_text
from app.security.checkout_reference import create_signed_checkout_reference


@dataclass(frozen=True, slots=True)
class RenderedMessagePackage:
    """Telegram-agnostic user-facing copy + action hints; no transport objects."""

    message_text: str
    action_keys: tuple[str, ...]
    correlation_id: str
    reply_markup: dict[str, Any] | None = None
    replay_suppresses_outbound: bool = False
    uc01_idempotency_key: str | None = None
    follow_up_messages: tuple["RenderedMessagePackage", ...] = ()


def _text_service_unavailable() -> str:
    return "Сервис временно недоступен. Пожалуйста, попробуйте позже."


_CATALOG_TEXT: dict[str, str] = {
    OutboundMessageKey.IDENTITY_READY.value: (
        "Добро пожаловать! Ваш чат подключён.\n"
        "Используйте /menu для просмотра тарифов и оформления подписки.\n"
        "Используйте /my_subscription, чтобы проверить текущий статус."
    ),
    OutboundMessageKey.NEEDS_ONBOARDING.value: (
        "Отправьте /start для регистрации, затем вы сможете использовать /status или /help. "
        "Бот должен распознать этот чат, прежде чем показать информацию о доступе."
    ),
    OutboundMessageKey.INACTIVE_OR_NOT_ELIGIBLE.value: (
        "Доступ для этого аккаунта сейчас недоступен. Если вы здесь впервые, отправьте /start, затем /status или /help. "
        "Эта версия не предоставляет новый доступ и не отправляет файлы."
    ),
    OutboundMessageKey.NEEDS_REVIEW.value: (
        "Доступ временно ограничен в связи с проверкой. Вы можете использовать /status или /help. "
        "Эта версия не отправляет файлы."
    ),
    OutboundMessageKey.SUBSCRIPTION_EXPIRED.value: (
        "Ваша подписка истекла.\n"
        "Используйте /renew для продления, затем проверьте /my_subscription."
    ),
    OutboundMessageKey.SUBSCRIPTION_ACTIVE.value: (
        "Ваша подписка активна.\n"
        "Используйте /my_subscription, чтобы узнать дату окончания."
    ),
    OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY.value: (
        "Ваша подписка активна, но инструкции по доступу ещё не готовы. "
        "Попробуйте /get_access немного позже."
    ),
    OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_READY.value: (
        "Ваша подписка активна и инструкции по доступу готовы. "
        "Используйте /get_access, чтобы получить их."
    ),
    OutboundMessageKey.SLICE1_HELP.value: (
        "Доступные команды:\n"
        "/start - подключить чат\n"
        "/menu - главное меню\n"
        "/plans - доступные тарифы\n"
        "/buy - оформить подписку\n"
        "/checkout - аналог /buy\n"
        "/success - что делать после оплаты\n"
        "/my_subscription - статус подписки (аналог /status)\n"
        "/status - статус подписки\n"
        "/renew - продлить подписку\n"
        "/support - помощь и FAQ\n"
        "/support_contact - контакты поддержки\n"
        "/resend_access - повторно получить инструкции доступа\n"
        "/get_access - аналог /resend_access\n"
        "/help - эта справка"
    ),
    OutboundMessageKey.INVALID_INPUT.value: "Ввод некорректен. Попробуйте снова.",
    OutboundMessageKey.TRY_AGAIN_LATER.value: (
        "Что-то пошло не так. Пожалуйста, попробуйте позже."
    ),
    OutboundMessageKey.SERVICE_UNAVAILABLE.value: _text_service_unavailable(),
    OutboundMessageKey.TELEGRAM_COMMAND_RATE_LIMITED.value: "Слишком много запросов. Пожалуйста, попробуйте позже.",
    OutboundMessageKey.RESEND_ACCESS_ACCEPTED.value: (
        "Запрос на получение инструкций доступа принят. Если доставка доступна, инструкции будут отправлены повторно."
    ),
    OutboundMessageKey.RESEND_ACCESS_NOT_ENABLED.value: (
        "Эта функция пока недоступна."
    ),
    OutboundMessageKey.RESEND_ACCESS_NOT_ELIGIBLE.value: (
        "Инструкции доступа нельзя повторно отправить для этого аккаунта.\n"
        "Если подписка неактивна или истекла, используйте /renew."
    ),
    OutboundMessageKey.RESEND_ACCESS_COOLDOWN.value: (
        "Подождите немного перед повторным запросом инструкций доступа."
    ),
    OutboundMessageKey.RESEND_ACCESS_NOT_READY.value: (
        "Инструкции доступа ещё не готовы для повторной отправки. Попробуйте позже."
    ),
    OutboundMessageKey.RESEND_ACCESS_TEMPORARILY_UNAVAILABLE.value: (
        "Повторная отправка инструкций временно недоступна. Попробуйте позже."
    ),
    OutboundMessageKey.STORE_MENU.value: (
        "Главное меню:\n"
        "/plans - посмотреть тарифы\n"
        "/buy - оформить подписку\n"
        "/my_subscription - проверить статус подписки\n"
        "/renew - продлить подписку\n"
        "/support - помощь и FAQ\n"
        "/support_contact - связаться с поддержкой"
    ),
    OutboundMessageKey.STORE_SUCCESS.value: (
        "Оплата получена.\n"
        "Активация может занять некоторое время.\n"
        "Используйте /my_subscription для проверки статуса, затем /get_access, когда подписка станет активной."
    ),
    OutboundMessageKey.STORE_SUCCESS_ACTIVE.value: (
        "Подписка активна.\n"
        "Используйте /my_subscription для проверки статуса и /get_access для получения инструкций доступа."
    ),
    OutboundMessageKey.STORE_PLANS.value: "Стоимость указана при оформлении. Используйте /buy для продолжения.",
    OutboundMessageKey.STORE_BUY.value: "Оплата пока не настроена, обратитесь в поддержку.",
    OutboundMessageKey.STORE_RENEW.value: "Продление пока не настроено, обратитесь в поддержку.",
    OutboundMessageKey.SUPPORT_MENU.value: "Меню поддержки.",
    OutboundMessageKey.SUPPORT_CONTACT.value: "Контакты поддержки.",
    OutboundMessageKey.FULFILLMENT_SUCCESS_NOTIFICATION.value: "Оплата успешно обработана.",
    OutboundMessageKey.SUBSCRIPTION_ACTIVE_CONFIRMATION.value: "",
}

_KNOWN_KEYS = frozenset(_CATALOG_TEXT.keys())


def _action_keys_from_plan(plan: TelegramOutboundPlan) -> tuple[str, ...]:
    """Expose action keys only for onboarding guidance when the plan supplies them."""
    if plan.message_key != OutboundMessageKey.NEEDS_ONBOARDING.value:
        return ()
    if plan.next_action_key:
        return (plan.next_action_key,)
    return ()


def _storefront_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [
            ["📋 Меню", "📊 Тарифы"],
            ["💳 Купить", "📱 Подписка"],
            ["🔄 Продлить", "🆘 Поддержка"],
            ["❓ Помощь"],
        ],
        "resize_keyboard": True,
    }


def _support_menu_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [["📞 Контакты"], ["📋 Меню"]],
        "resize_keyboard": True,
    }


def _support_contact_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [["📋 Меню"]],
        "resize_keyboard": True,
    }


def _fulfillment_success_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [["🔓 Доступ"], ["📋 Меню"]],
        "resize_keyboard": True,
    }


def _format_fulfillment_success_notification_text(*, active_until_ymd: str | None) -> str:
    lines = [
        "Оплата получена ✅",
        "",
        "Ваша подписка теперь активна.",
    ]
    if active_until_ymd:
        lines.extend(["", f"Действует до: {active_until_ymd}"])
    lines.extend(
        [
            "",
            "Дальнейшие действия:",
            "/get_access — получить инструкции доступа",
            "/menu — открыть главное меню",
        ]
    )
    return "\n".join(lines)


def _format_subscription_active_confirmation_text(*, active_until_ymd: str | None) -> str:
    lines = [
        "Ваша подписка активна ✅",
        "",
        "Всё в порядке, доступ открыт.",
    ]
    if active_until_ymd:
        lines.extend(["", f"Действует до: {active_until_ymd}"])
    lines.extend(
        [
            "",
            "Дальнейшие действия:",
            "/get_access — получить инструкции доступа",
            "/menu — главное меню",
        ]
    )
    return "\n".join(lines)


def _format_plans_copy() -> str:
    cfg = load_storefront_public_config()
    if cfg.plan_name and cfg.plan_price:
        return f"Тариф: {cfg.plan_name}\nСтоимость: {cfg.plan_price}\nИспользуйте /buy для оформления подписки."
    if cfg.plan_name:
        return (
            f"Тариф: {cfg.plan_name}\n"
            "Стоимость указана при оформлении.\n"
            "Используйте /buy для продолжения."
        )
    if cfg.plan_price:
        return f"Стоимость тарифа: {cfg.plan_price}\nИспользуйте /buy для оформления подписки."
    return "Стоимость указана при оформлении. Используйте /buy для продолжения."


def _format_buy_copy(*, telegram_user_id: int | None) -> str:
    cfg = load_storefront_public_config()
    if cfg.checkout_url is None:
        return "Оплата пока не настроена, обратитесь в поддержку."
    if telegram_user_id is None:
        return "Оплата пока не настроена, обратитесь в поддержку."
    secret = load_checkout_reference_secret()
    if not secret:
        return "Оплата пока не настроена, обратитесь в поддержку."
    signed = create_signed_checkout_reference(
        telegram_user_id=telegram_user_id,
        internal_user_id=f"u{telegram_user_id}",
        secret=secret,
    )
    checkout_url = build_checkout_url_with_reference(
        base_url=cfg.checkout_url,
        client_reference_id=signed.reference_id,
        client_reference_proof=signed.reference_proof,
    )
    if checkout_url is None:
        return "Оплата пока не настроена, обратитесь в поддержку."
    return f"Оформить подписку: {checkout_url}"


def _format_status_active_copy(message_key: str, active_until_ymd: str | None) -> str:
    until = f"Ваша подписка активна до {active_until_ymd}." if active_until_ymd else "Ваша подписка активна."
    if message_key == OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY.value:
        return until + "\nИнструкции доступа ещё не готовы. Попробуйте /get_access немного позже."
    if message_key == OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_READY.value:
        return until + "\nИнструкции доступа готовы. Используйте /get_access."
    return until


def _format_renew_copy(*, telegram_user_id: int | None) -> str:
    cfg = load_storefront_public_config()
    base_url = cfg.renewal_url or cfg.checkout_url
    if base_url is None or telegram_user_id is None:
        return "Продление пока не настроено, обратитесь в поддержку."
    secret = load_checkout_reference_secret()
    if not secret:
        return "Продление пока не настроено, обратитесь в поддержку."
    signed = create_signed_checkout_reference(
        telegram_user_id=telegram_user_id,
        internal_user_id=f"u{telegram_user_id}",
        secret=secret,
    )
    url = build_checkout_url_with_reference(
        base_url=base_url,
        client_reference_id=signed.reference_id,
        client_reference_proof=signed.reference_proof,
    )
    if url is None:
        return "Продление пока не настроено, обратитесь в поддержку."
    return f"Продлить подписку: {url}"


def render_telegram_outbound_plan(
    plan: TelegramOutboundPlan,
    *,
    telegram_user_id: int | None = None,
) -> RenderedMessagePackage:
    """Map an outbound plan to neutral rendered text; unknown keys fail closed to outage copy."""
    key = plan.message_key
    if key not in _KNOWN_KEYS:
        return RenderedMessagePackage(
            message_text=_text_service_unavailable(),
            action_keys=(),
            correlation_id=plan.correlation_id,
            replay_suppresses_outbound=plan.replay_suppresses_outbound,
            uc01_idempotency_key=plan.uc01_idempotency_key,
            follow_up_messages=(),
        )
    keyboard: dict[str, Any] | None = None
    if plan.keyboard_marker == OutboundKeyboardMarker.STOREFRONT_MAIN.value:
        keyboard = _storefront_keyboard()
    elif plan.keyboard_marker == OutboundKeyboardMarker.SUPPORT_MENU.value:
        keyboard = _support_menu_keyboard()
    elif plan.keyboard_marker == OutboundKeyboardMarker.SUPPORT_CONTACT.value:
        keyboard = _support_contact_keyboard()
    elif plan.keyboard_marker == OutboundKeyboardMarker.FULFILLMENT_SUCCESS.value:
        keyboard = _fulfillment_success_keyboard()
    text = _CATALOG_TEXT[key]
    if key == OutboundMessageKey.STORE_PLANS.value:
        text = _format_plans_copy()
    elif key == OutboundMessageKey.STORE_BUY.value:
        text = _format_buy_copy(telegram_user_id=telegram_user_id)
    elif key == OutboundMessageKey.STORE_RENEW.value:
        text = _format_renew_copy(telegram_user_id=telegram_user_id)
    elif key == OutboundMessageKey.SUPPORT_MENU.value:
        text = build_support_menu_text()
    elif key == OutboundMessageKey.SUPPORT_CONTACT.value:
        text = build_support_contact_text(load_storefront_public_config())
    elif key == OutboundMessageKey.FULFILLMENT_SUCCESS_NOTIFICATION.value:
        text = _format_fulfillment_success_notification_text(active_until_ymd=plan.active_until_ymd)
    elif key == OutboundMessageKey.SUBSCRIPTION_ACTIVE_CONFIRMATION.value:
        text = _format_subscription_active_confirmation_text(active_until_ymd=plan.active_until_ymd)
    elif key in (
        OutboundMessageKey.SUBSCRIPTION_ACTIVE.value,
        OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY.value,
        OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_READY.value,
    ):
        text = _format_status_active_copy(key, plan.active_until_ymd)
    return RenderedMessagePackage(
        message_text=text,
        action_keys=_action_keys_from_plan(plan),
        correlation_id=plan.correlation_id,
        reply_markup=keyboard,
        replay_suppresses_outbound=plan.replay_suppresses_outbound,
        uc01_idempotency_key=plan.uc01_idempotency_key,
        follow_up_messages=(),
    )
