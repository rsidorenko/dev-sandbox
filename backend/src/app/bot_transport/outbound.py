"""Чистый маппинг исходящего плана Telegram (slice 1): TransportSafeResponse → только ключи каталога.

Без Telegram SDK, без сети, без пользовательского текста — стабильные ключи для будущего каталога сообщений.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.bot_transport.presentation import (
    TransportAccessResendCode,
    TransportBootstrapCode,
    TransportErrorCode,
    TransportHelpCode,
    TransportNextActionHint,
    TransportResponseCategory,
    TransportSafeResponse,
    TransportStorefrontCode,
    TransportSupportCode,
    TransportStatusCode,
)


class OutboundPlanCategory(str, Enum):
    """Тип плана верхнего уровня, согласованный с транспортными категориями (только маршрутизация каталога)."""

    SUCCESS = "success"
    GUIDANCE = "guidance"
    ERROR = "error"


class OutboundMessageKey(str, Enum):
    """Стабильные ключи каталога сообщений; значения — идентификаторы поиска (без текста)."""

    IDENTITY_READY = "identity_ready"
    NEEDS_ONBOARDING = "needs_onboarding"
    INACTIVE_OR_NOT_ELIGIBLE = "inactive_or_not_eligible"
    NEEDS_REVIEW = "needs_review"
    SUBSCRIPTION_EXPIRED = "subscription_expired"
    SUBSCRIPTION_ACTIVE = "subscription_active"
    SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY = "subscription_active_access_not_ready"
    SUBSCRIPTION_ACTIVE_ACCESS_READY = "subscription_active_access_ready"
    INVALID_INPUT = "invalid_input"
    TRY_AGAIN_LATER = "try_again_later"
    SERVICE_UNAVAILABLE = "service_unavailable"
    SLICE1_HELP = "slice1_help"
    RESEND_ACCESS_ACCEPTED = "resend_access_accepted"
    RESEND_ACCESS_NOT_ENABLED = "resend_access_not_enabled"
    RESEND_ACCESS_NOT_ELIGIBLE = "resend_access_not_eligible"
    RESEND_ACCESS_COOLDOWN = "resend_access_cooldown"
    RESEND_ACCESS_NOT_READY = "resend_access_not_ready"
    RESEND_ACCESS_TEMPORARILY_UNAVAILABLE = "resend_access_temporarily_unavailable"
    TELEGRAM_COMMAND_RATE_LIMITED = "telegram_command_rate_limited"
    STORE_MENU = "store_menu"
    STORE_PLANS = "store_plans"
    STORE_BUY = "store_buy"
    STORE_SUCCESS = "store_success"
    STORE_SUCCESS_ACTIVE = "store_success_active"
    STORE_RENEW = "store_renew"
    SUPPORT_MENU = "support_menu"
    SUPPORT_CONTACT = "support_contact"
    FULFILLMENT_SUCCESS_NOTIFICATION = "fulfillment_success_notification"
    SUBSCRIPTION_ACTIVE_CONFIRMATION = "subscription_active_confirmation"


class OutboundNextActionKey(str, Enum):
    COMPLETE_BOOTSTRAP = "complete_bootstrap"


class OutboundKeyboardMarker(str, Enum):
    """Нетекстовая подсказка, какую клавиатуру ответа / форму строки действий использовать (если есть)."""

    NONE = "none"
    PRIMARY_ONBOARDING = "primary_onboarding"
    STOREFRONT_MAIN = "storefront_main"
    SUPPORT_MENU = "support_menu"
    SUPPORT_CONTACT = "support_contact"
    FULFILLMENT_SUCCESS = "fulfillment_success"


@dataclass(frozen=True, slots=True)
class TelegramOutboundPlan:
    """План отправки для Telegram: только ключи, безопасен для тонкого runtime + каталог сообщений."""

    category: OutboundPlanCategory
    message_key: str
    next_action_key: str | None
    keyboard_marker: str
    correlation_id: str
    replay_suppresses_outbound: bool = False
    uc01_idempotency_key: str | None = None
    active_until_ymd: str | None = None


def _error_plan(
    message_key: OutboundMessageKey,
    correlation_id: str,
) -> TelegramOutboundPlan:
    return TelegramOutboundPlan(
        category=OutboundPlanCategory.ERROR,
        message_key=message_key.value,
        next_action_key=None,
        keyboard_marker=OutboundKeyboardMarker.NONE.value,
        correlation_id=correlation_id,
        replay_suppresses_outbound=False,
        uc01_idempotency_key=None,
    )


def _service_unavailable_plan(correlation_id: str) -> TelegramOutboundPlan:
    return _error_plan(OutboundMessageKey.SERVICE_UNAVAILABLE, correlation_id)


def build_fulfillment_success_notification_plan(
    *,
    correlation_id: str,
    active_until_ymd: str | None,
) -> TelegramOutboundPlan:
    """Исходящий план для проактивного уведомления Telegram после checkout (не инициирован командой)."""
    return TelegramOutboundPlan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.FULFILLMENT_SUCCESS_NOTIFICATION.value,
        next_action_key=None,
        keyboard_marker=OutboundKeyboardMarker.FULFILLMENT_SUCCESS.value,
        correlation_id=correlation_id,
        active_until_ymd=active_until_ymd,
    )


def build_subscription_active_recovery_confirmation_plan(transport: TransportSafeResponse) -> TelegramOutboundPlan:
    """Второе исходящее сообщение после UC-02 status, когда окно подписки ещё действует (инициировано командой)."""
    return TelegramOutboundPlan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.SUBSCRIPTION_ACTIVE_CONFIRMATION.value,
        next_action_key=None,
        keyboard_marker=OutboundKeyboardMarker.FULFILLMENT_SUCCESS.value,
        correlation_id=transport.correlation_id,
        active_until_ymd=transport.active_until_ymd,
    )


def map_transport_safe_to_outbound_plan(transport: TransportSafeResponse) -> TelegramOutboundPlan:
    """Отображает безопасный транспортный ответ в исходящий план Telegram (только ключи).

    Повтор UC-01 bootstrap устанавливает ``replay_suppresses_outbound`` в плане, чтобы runtime мог
    пропустить дублированную пользовательскую отправку для того же Telegram ``update_id``.

    Неизвестные или неконсистентные коды маппятся в общий безопасный класс сбоя (fail-closed).
    """
    cid = transport.correlation_id
    cat = transport.category
    code = transport.code

    if cat is TransportResponseCategory.SUCCESS:
        if code == TransportBootstrapCode.IDENTITY_READY.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.IDENTITY_READY.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.STOREFRONT_MAIN.value,
                correlation_id=cid,
                replay_suppresses_outbound=transport.replay_suppresses_outbound,
                uc01_idempotency_key=transport.uc01_idempotency_key,
                active_until_ymd=transport.active_until_ymd,
            )
        if code == TransportHelpCode.SLICE1_HELP.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.SLICE1_HELP.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.STOREFRONT_MAIN.value,
                correlation_id=cid,
                replay_suppresses_outbound=False,
                uc01_idempotency_key=None,
                active_until_ymd=transport.active_until_ymd,
            )
        if code == TransportStatusCode.SUBSCRIPTION_EXPIRED.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.SUBSCRIPTION_EXPIRED.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
                active_until_ymd=transport.active_until_ymd,
            )
        if code == TransportStorefrontCode.STORE_MENU.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.STORE_MENU.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.STOREFRONT_MAIN.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
                active_until_ymd=transport.active_until_ymd,
            )
        if code == TransportStorefrontCode.STORE_PLANS.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.STORE_PLANS.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.STOREFRONT_MAIN.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
                active_until_ymd=transport.active_until_ymd,
            )
        if code == TransportStorefrontCode.STORE_BUY.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.STORE_BUY.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.STOREFRONT_MAIN.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
                active_until_ymd=transport.active_until_ymd,
            )
        if code == TransportStorefrontCode.STORE_SUCCESS.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.STORE_SUCCESS.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.STOREFRONT_MAIN.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        if code == TransportStorefrontCode.STORE_SUCCESS_ACTIVE.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.STORE_SUCCESS_ACTIVE.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.STOREFRONT_MAIN.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        if code == TransportStorefrontCode.STORE_RENEW.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.STORE_RENEW.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.STOREFRONT_MAIN.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        if code == TransportSupportCode.SUPPORT_MENU.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.SUPPORT_MENU.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.SUPPORT_MENU.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        if code == TransportSupportCode.SUPPORT_CONTACT.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.SUPPORT_CONTACT.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.SUPPORT_CONTACT.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        if code == TransportAccessResendCode.RESEND_ACCEPTED.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.RESEND_ACCESS_ACCEPTED.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        if code == TransportAccessResendCode.NOT_ENABLED.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.RESEND_ACCESS_NOT_ENABLED.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        if code == TransportAccessResendCode.NOT_ELIGIBLE.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.RESEND_ACCESS_NOT_ELIGIBLE.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        if code == TransportAccessResendCode.COOLDOWN.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.RESEND_ACCESS_COOLDOWN.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        if code == TransportAccessResendCode.NOT_READY.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.RESEND_ACCESS_NOT_READY.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        if code == TransportAccessResendCode.TEMPORARILY_UNAVAILABLE.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.RESEND_ACCESS_TEMPORARILY_UNAVAILABLE.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        if code == TransportStatusCode.INACTIVE_OR_NOT_ELIGIBLE.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.INACTIVE_OR_NOT_ELIGIBLE.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
                active_until_ymd=transport.active_until_ymd,
            )
        if code == TransportStatusCode.NEEDS_REVIEW.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.NEEDS_REVIEW.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
                active_until_ymd=transport.active_until_ymd,
            )
        if code == TransportStatusCode.SUBSCRIPTION_ACTIVE.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.SUBSCRIPTION_ACTIVE.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
                active_until_ymd=transport.active_until_ymd,
            )
        if code == TransportStatusCode.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
                active_until_ymd=transport.active_until_ymd,
            )
        if code == TransportStatusCode.SUBSCRIPTION_ACTIVE_ACCESS_READY.value:
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.SUCCESS,
                message_key=OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_READY.value,
                next_action_key=None,
                keyboard_marker=OutboundKeyboardMarker.NONE.value,
                correlation_id=cid,
                uc01_idempotency_key=None,
                active_until_ymd=transport.active_until_ymd,
            )
        return _service_unavailable_plan(cid)

    if cat is TransportResponseCategory.GUIDANCE:
        if code == TransportStatusCode.NEEDS_ONBOARDING.value:
            next_key: str | None = None
            marker = OutboundKeyboardMarker.NONE.value
            if transport.next_action_hint == TransportNextActionHint.COMPLETE_BOOTSTRAP.value:
                next_key = OutboundNextActionKey.COMPLETE_BOOTSTRAP.value
                marker = OutboundKeyboardMarker.PRIMARY_ONBOARDING.value
            return TelegramOutboundPlan(
                category=OutboundPlanCategory.GUIDANCE,
                message_key=OutboundMessageKey.NEEDS_ONBOARDING.value,
                next_action_key=next_key,
                keyboard_marker=marker,
                correlation_id=cid,
                uc01_idempotency_key=None,
            )
        return _service_unavailable_plan(cid)

    if cat is TransportResponseCategory.ERROR:
        if code == TransportErrorCode.INVALID_INPUT.value:
            return _error_plan(OutboundMessageKey.INVALID_INPUT, cid)
        if code == TransportErrorCode.TRY_AGAIN_LATER.value:
            return _error_plan(OutboundMessageKey.TRY_AGAIN_LATER, cid)
        if code == TransportErrorCode.SERVICE_UNAVAILABLE.value:
            return _error_plan(OutboundMessageKey.SERVICE_UNAVAILABLE, cid)
        if code == TransportErrorCode.TELEGRAM_COMMAND_RATE_LIMITED.value:
            return _error_plan(OutboundMessageKey.TELEGRAM_COMMAND_RATE_LIMITED, cid)
        return _service_unavailable_plan(cid)

    return _service_unavailable_plan(cid)
