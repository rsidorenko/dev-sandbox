"""Нормализация транспорта slice 1: разрешённые команды → входы обработчиков (без сырых нагрузок)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.application.handlers import BootstrapIdentityInput, GetSubscriptionStatusInput
from app.application.telegram_access_resend import (
    TelegramAccessResendInput,
    TelegramAccessResendSourceCommand,
)
from app.security.validation import (
    ValidationError,
    validate_telegram_update_id,
    validate_telegram_user_id,
)
from app.shared.correlation import require_correlation_id

_MAX_LINE_LEN = 512
_MAX_COMMAND_TOKEN_LEN = 64
_REF_START_RE = __import__("re").compile(r"^ref_([a-z0-9]{4,32})$", __import__("re").IGNORECASE)

_SLICE1_BOOTSTRAP_COMMANDS: frozenset[str] = frozenset({"/start"})
_SLICE1_STATUS_COMMANDS: frozenset[str] = frozenset({"/status", "/my_subscription"})
_SLICE1_MENU_COMMANDS: frozenset[str] = frozenset({"/menu"})
_SLICE1_HELP_COMMANDS: frozenset[str] = frozenset({"/help"})
_SLICE1_RESEND_COMMANDS: frozenset[str] = frozenset({"/resend_access", "/get_access"})
_SLICE1_PLANS_COMMANDS: frozenset[str] = frozenset({"/plans"})
_SLICE1_BUY_COMMANDS: frozenset[str] = frozenset({"/buy", "/checkout"})
_SLICE1_SUCCESS_COMMANDS: frozenset[str] = frozenset({"/success"})
_SLICE1_RENEW_COMMANDS: frozenset[str] = frozenset({"/renew"})
_SLICE1_SUPPORT_MENU_COMMANDS: frozenset[str] = frozenset({"/support"})
_SLICE1_SUPPORT_CONTACT_COMMANDS: frozenset[str] = frozenset({"/support_contact"})


@dataclass(frozen=True, slots=True)
class TransportIncomingEnvelope:
    """
    Общая входная обёртка slice 1: идентификаторы + ограниченная нормализованная команда.
    Без объектов Telegram update или непрозрачных нагрузок.
    """

    telegram_user_id: int
    correlation_id: str
    telegram_update_id: int | None
    normalized_command_text: str | None
    callback_data: str | None = None
    start_param: str | None = None


class NormalizationRejectReason(str, Enum):
    """Безопасные, низкокардинальные категории отклонения для нормализации транспорта."""

    UNKNOWN_COMMAND = "unknown_command"
    INVALID_INPUT = "invalid_input"
    MISSING_EVENT_ID_FOR_BOOTSTRAP = "missing_event_id_for_bootstrap"
    MISSING_EVENT_ID_FOR_RESEND = "missing_event_id_for_resend"


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Bootstrap:
    input: BootstrapIdentityInput
    referral_code: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedCallback:
    """Parsed inline callback button press."""
    action: str
    data: str | None = None
    correlation_id: str = ""


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Status:
    input: GetSubscriptionStatusInput


@dataclass(frozen=True, slots=True)
class NormalizedSlice1ResendAccess:
    input: TelegramAccessResendInput


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Help:
    """Только для чтения /help: correlation id только для транспорта; без входов обработчика приложения."""

    correlation_id: str


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Menu:
    """Только для чтения /menu: показывает главное меню магазина; без обработчика, без изменения состояния."""

    correlation_id: str


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Plans:
    correlation_id: str


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Buy:
    correlation_id: str


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Success:
    correlation_id: str


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Renew:
    correlation_id: str


@dataclass(frozen=True, slots=True)
class NormalizedSlice1SupportMenu:
    """Только для чтения /support: меню FAQ; без обработчика, без изменения состояния."""

    correlation_id: str


@dataclass(frozen=True, slots=True)
class NormalizedSlice1SupportContact:
    """Только для чтения /support_contact: безопасные контактные данные; без обработчика, без изменения состояния."""

    correlation_id: str


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Rejected:
    reason: NormalizationRejectReason


NormalizedSlice1Result = (
    NormalizedSlice1Bootstrap
    | NormalizedSlice1Status
    | NormalizedSlice1ResendAccess
    | NormalizedSlice1Menu
    | NormalizedSlice1Help
    | NormalizedSlice1Plans
    | NormalizedSlice1Buy
    | NormalizedSlice1Success
    | NormalizedSlice1Renew
    | NormalizedSlice1SupportMenu
    | NormalizedSlice1SupportContact
    | NormalizedCallback
    | NormalizedSlice1Rejected
)


def normalize_command_token(raw: str | None) -> str | None:
    """
    Извлекает ограниченную первую токен-команду (напр. /start, /start@bot → /start).
    Возвращает None если ввод непригоден; не сохраняет и не повторяет полные тела сообщений.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if len(s) > _MAX_LINE_LEN:
        return None
    first = s.split()[0]
    if "@" in first:
        first = first.split("@", 1)[0]
    if len(first) > _MAX_COMMAND_TOKEN_LEN:
        return None
    return first.lower()


def _parse_start_param(command_text: str | None) -> str | None:
    if command_text is None:
        return None
    parts = command_text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    match = _REF_START_RE.match(parts[1].strip())
    return match.group(1) if match else None


def parse_slice1_transport(envelope: TransportIncomingEnvelope) -> NormalizedSlice1Result:
    """Отображает разрешённые команды slice 1 и inline callback'и во входы обработчиков."""
    # Callback takes priority over text commands
    if envelope.callback_data:
        return NormalizedCallback(
            action=envelope.callback_data,
            data=None,
            correlation_id=envelope.correlation_id,
        )

    try:
        require_correlation_id(envelope.correlation_id)
    except ValueError:
        return NormalizedSlice1Rejected(reason=NormalizationRejectReason.INVALID_INPUT)

    try:
        validate_telegram_user_id(envelope.telegram_user_id)
    except ValidationError:
        return NormalizedSlice1Rejected(reason=NormalizationRejectReason.INVALID_INPUT)

    token = normalize_command_token(envelope.normalized_command_text)
    if token is None:
        return NormalizedSlice1Rejected(reason=NormalizationRejectReason.INVALID_INPUT)

    if token in _SLICE1_BOOTSTRAP_COMMANDS:
        if envelope.telegram_update_id is None:
            return NormalizedSlice1Rejected(
                reason=NormalizationRejectReason.MISSING_EVENT_ID_FOR_BOOTSTRAP,
            )
        try:
            validate_telegram_update_id(envelope.telegram_update_id)
        except ValidationError:
            return NormalizedSlice1Rejected(reason=NormalizationRejectReason.INVALID_INPUT)
        ref_code = _parse_start_param(envelope.normalized_command_text)
        return NormalizedSlice1Bootstrap(
            input=BootstrapIdentityInput(
                telegram_user_id=envelope.telegram_user_id,
                telegram_update_id=envelope.telegram_update_id,
                correlation_id=envelope.correlation_id,
            ),
            referral_code=ref_code,
        )

    if token in _SLICE1_STATUS_COMMANDS:
        return NormalizedSlice1Status(
            input=GetSubscriptionStatusInput(
                telegram_user_id=envelope.telegram_user_id,
                correlation_id=envelope.correlation_id,
            ),
        )

    if token in _SLICE1_RESEND_COMMANDS:
        if envelope.telegram_update_id is None:
            return NormalizedSlice1Rejected(
                reason=NormalizationRejectReason.MISSING_EVENT_ID_FOR_RESEND,
            )
        try:
            validate_telegram_update_id(envelope.telegram_update_id)
        except ValidationError:
            return NormalizedSlice1Rejected(reason=NormalizationRejectReason.INVALID_INPUT)
        source_command = (
            TelegramAccessResendSourceCommand.RESEND_ACCESS
            if token == "/resend_access"
            else TelegramAccessResendSourceCommand.GET_ACCESS
        )
        return NormalizedSlice1ResendAccess(
            input=TelegramAccessResendInput(
                telegram_user_id=envelope.telegram_user_id,
                telegram_update_id=envelope.telegram_update_id,
                correlation_id=envelope.correlation_id,
                source_command=source_command,
            ),
        )

    if token in _SLICE1_MENU_COMMANDS:
        return NormalizedSlice1Menu(correlation_id=envelope.correlation_id)
    if token in _SLICE1_HELP_COMMANDS:
        return NormalizedSlice1Help(correlation_id=envelope.correlation_id)
    if token in _SLICE1_PLANS_COMMANDS:
        return NormalizedSlice1Plans(correlation_id=envelope.correlation_id)
    if token in _SLICE1_BUY_COMMANDS:
        return NormalizedSlice1Buy(correlation_id=envelope.correlation_id)
    if token in _SLICE1_SUCCESS_COMMANDS:
        return NormalizedSlice1Success(correlation_id=envelope.correlation_id)
    if token in _SLICE1_RENEW_COMMANDS:
        return NormalizedSlice1Renew(correlation_id=envelope.correlation_id)
    if token in _SLICE1_SUPPORT_MENU_COMMANDS:
        return NormalizedSlice1SupportMenu(correlation_id=envelope.correlation_id)
    if token in _SLICE1_SUPPORT_CONTACT_COMMANDS:
        return NormalizedSlice1SupportContact(correlation_id=envelope.correlation_id)

    return NormalizedSlice1Rejected(reason=NormalizationRejectReason.UNKNOWN_COMMAND)
