"""Отображает результаты обработчиков в безопасные транспортные ответы (только категории/коды; без продуктового текста)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.application.handlers import BootstrapIdentityResult, GetSubscriptionStatusResult
from app.application.telegram_access_resend import (
    TelegramAccessResendOutcome,
    TelegramAccessResendResult,
)
from app.security.errors import UserSafeErrorCode
from app.shared.types import OperationOutcomeCategory, SafeUserStatusCategory


class TransportResponseCategory(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    GUIDANCE = "guidance"


class TransportBootstrapCode(str, Enum):
    """Стабильные коды исхода bootstrap для транспорта (успешные пути объединены)."""

    IDENTITY_READY = "identity_ready"


class TransportStatusCode(str, Enum):
    """Стабильные сводные коды UC-02 (fail-closed; без заявлений о биллинге или провайдере)."""

    NEEDS_ONBOARDING = "needs_onboarding"
    INACTIVE_OR_NOT_ELIGIBLE = "inactive_or_not_eligible"
    NEEDS_REVIEW = "needs_review"
    SUBSCRIPTION_EXPIRED = "subscription_expired"
    SUBSCRIPTION_ACTIVE = "subscription_active"
    SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY = "subscription_active_access_not_ready"
    SUBSCRIPTION_ACTIVE_ACCESS_READY = "subscription_active_access_ready"


class TransportErrorCode(str, Enum):
    """Стабильные коды классов ошибок, согласованные с пользовательской таксономией (без внутренних деталей)."""

    INVALID_INPUT = "invalid_input"
    TRY_AGAIN_LATER = "try_again_later"
    SERVICE_UNAVAILABLE = "service_unavailable"
    TELEGRAM_COMMAND_RATE_LIMITED = "telegram_command_rate_limited"


class TransportHelpCode(str, Enum):
    """Справка slice 1 только для чтения; без обработчика приложения, без изменения состояния."""

    SLICE1_HELP = "slice1_help"


class TransportStorefrontCode(str, Enum):
    STORE_MENU = "store_menu"
    STORE_PLANS = "store_plans"
    STORE_BUY = "store_buy"
    STORE_SUCCESS = "store_success"
    STORE_SUCCESS_ACTIVE = "store_success_active"
    STORE_RENEW = "store_renew"


class TransportSupportCode(str, Enum):
    """Поверхности поддержки только для чтения; без биллинга или выдачи."""

    SUPPORT_MENU = "support_menu"
    SUPPORT_CONTACT = "support_contact"


class TransportAccessResendCode(str, Enum):
    NOT_ENABLED = "resend_access_not_enabled"
    RESEND_ACCEPTED = "resend_access_accepted"
    NOT_ELIGIBLE = "resend_access_not_eligible"
    COOLDOWN = "resend_access_cooldown"
    NOT_READY = "resend_access_not_ready"
    TEMPORARILY_UNAVAILABLE = "resend_access_temporarily_unavailable"


class TransportNextActionHint(str, Enum):
    COMPLETE_BOOTSTRAP = "complete_bootstrap"


@dataclass(frozen=True, slots=True)
class TransportSafeResponse:
    category: TransportResponseCategory
    code: str
    correlation_id: str
    next_action_hint: str | None = None
    #: Только UC-01: повтор того же Telegram update обработан идемпотентно; runtime может пропустить дублированную отправку.
    replay_suppresses_outbound: bool = False
    #: Только при успехе UC-01: ключ-дайджест, согласованный с ``idempotency_records`` для исходящего delivery ledger.
    uc01_idempotency_key: str | None = None
    active_until_ymd: str | None = None
    #: UC-02 /status и /my_subscription: второе исходящее сообщение с восстановительным текстом стиля успеха (только чтение).
    subscription_active_recovery_followup: bool = False


def _error_code_from_user_safe(code: UserSafeErrorCode | None) -> str:
    if code is None:
        return TransportErrorCode.SERVICE_UNAVAILABLE.value
    if code is UserSafeErrorCode.INVALID_INPUT:
        return TransportErrorCode.INVALID_INPUT.value
    if code is UserSafeErrorCode.TRY_AGAIN_LATER:
        return TransportErrorCode.TRY_AGAIN_LATER.value
    if code is UserSafeErrorCode.NOT_REGISTERED:
        return TransportErrorCode.INVALID_INPUT.value
    return TransportErrorCode.SERVICE_UNAVAILABLE.value


def _transport_error(
    category: TransportResponseCategory,
    user_safe: UserSafeErrorCode | None,
    correlation_id: str,
) -> TransportSafeResponse:
    return TransportSafeResponse(
        category=category,
        code=_error_code_from_user_safe(user_safe),
        correlation_id=correlation_id,
        next_action_hint=None,
        replay_suppresses_outbound=False,
        uc01_idempotency_key=None,
    )


def _status_code_for_safe_category(status: SafeUserStatusCategory) -> str:
    if status is SafeUserStatusCategory.NEEDS_BOOTSTRAP:
        return TransportStatusCode.NEEDS_ONBOARDING.value
    if status is SafeUserStatusCategory.NEEDS_REVIEW:
        return TransportStatusCode.NEEDS_REVIEW.value
    if status is SafeUserStatusCategory.SUBSCRIPTION_EXPIRED:
        return TransportStatusCode.SUBSCRIPTION_EXPIRED.value
    if status is SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY:
        return TransportStatusCode.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY.value
    if status is SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_READY:
        return TransportStatusCode.SUBSCRIPTION_ACTIVE_ACCESS_READY.value
    if status is SafeUserStatusCategory.SUBSCRIPTION_ACTIVE:
        return TransportStatusCode.SUBSCRIPTION_ACTIVE.value
    return TransportStatusCode.INACTIVE_OR_NOT_ELIGIBLE.value


def map_bootstrap_identity_to_transport(result: BootstrapIdentityResult) -> TransportSafeResponse:
    """Отображает результат UC-01 в безопасный транспортный ответ; повтор использует коды успеха, но может подавить исходящую отправку."""
    cid = result.correlation_id
    if result.outcome is OperationOutcomeCategory.SUCCESS:
        return TransportSafeResponse(
            category=TransportResponseCategory.SUCCESS,
            code=TransportBootstrapCode.IDENTITY_READY.value,
            correlation_id=cid,
            next_action_hint=None,
            replay_suppresses_outbound=result.idempotent_replay,
            uc01_idempotency_key=result.uc01_idempotency_key,
        )
    return _transport_error(TransportResponseCategory.ERROR, result.user_safe, cid)


def map_slice1_help_to_transport(correlation_id: str) -> TransportSafeResponse:
    """Отображает /help в успешный транспортный путь без вызова обработчиков UC-01/UC-02."""
    return TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportHelpCode.SLICE1_HELP.value,
        correlation_id=correlation_id,
        next_action_hint=None,
        replay_suppresses_outbound=False,
        uc01_idempotency_key=None,
    )


def map_slice1_storefront_to_transport(
    code: TransportStorefrontCode,
    correlation_id: str,
) -> TransportSafeResponse:
    return TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=code.value,
        correlation_id=correlation_id,
        next_action_hint=None,
        replay_suppresses_outbound=False,
        uc01_idempotency_key=None,
    )


def map_slice1_support_to_transport(
    code: TransportSupportCode,
    correlation_id: str,
) -> TransportSafeResponse:
    """Отображает /support и /support_contact только для чтения в транспорт (без обработчиков)."""
    return TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=code.value,
        correlation_id=correlation_id,
        next_action_hint=None,
        replay_suppresses_outbound=False,
        uc01_idempotency_key=None,
    )


def map_get_subscription_status_to_transport(
    result: GetSubscriptionStatusResult,
) -> TransportSafeResponse:
    """Отображает результат UC-02; неизвестный пользователь получает руководство по onboarding; неактивный — fail-closed."""
    cid = result.correlation_id
    oc = result.outcome

    if oc is OperationOutcomeCategory.SUCCESS:
        return TransportSafeResponse(
            category=TransportResponseCategory.SUCCESS,
            code=_status_code_for_safe_category(result.safe_status),
            correlation_id=cid,
            next_action_hint=None,
            replay_suppresses_outbound=False,
            uc01_idempotency_key=None,
            active_until_ymd=(
                result.active_until_utc.date().isoformat() if result.active_until_utc is not None else None
            ),
        )

    if oc is OperationOutcomeCategory.NOT_FOUND:
        return TransportSafeResponse(
            category=TransportResponseCategory.GUIDANCE,
            code=TransportStatusCode.NEEDS_ONBOARDING.value,
            correlation_id=cid,
            next_action_hint=TransportNextActionHint.COMPLETE_BOOTSTRAP.value,
            replay_suppresses_outbound=False,
            uc01_idempotency_key=None,
        )

    return _transport_error(TransportResponseCategory.ERROR, result.user_safe, cid)


def map_access_resend_to_transport(result: TelegramAccessResendResult) -> TransportSafeResponse:
    cid = result.correlation_id
    if result.outcome is TelegramAccessResendOutcome.NOT_ENABLED:
        code = TransportAccessResendCode.NOT_ENABLED.value
    elif result.outcome is TelegramAccessResendOutcome.RESEND_ACCEPTED:
        code = TransportAccessResendCode.RESEND_ACCEPTED.value
    elif result.outcome is TelegramAccessResendOutcome.NOT_ELIGIBLE:
        code = TransportAccessResendCode.NOT_ELIGIBLE.value
    elif result.outcome is TelegramAccessResendOutcome.COOLDOWN:
        code = TransportAccessResendCode.COOLDOWN.value
    elif result.outcome is TelegramAccessResendOutcome.NOT_READY:
        code = TransportAccessResendCode.NOT_READY.value
    else:
        code = TransportAccessResendCode.TEMPORARILY_UNAVAILABLE.value
    return TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=code,
        correlation_id=cid,
        next_action_hint=None,
        replay_suppresses_outbound=False,
        uc01_idempotency_key=None,
    )
