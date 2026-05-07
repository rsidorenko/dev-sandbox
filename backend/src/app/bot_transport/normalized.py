"""Slice-1 transport normalization: allowlisted commands → handler inputs (no raw payloads)."""

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
    Generic slice-1 ingress envelope: identifiers + bounded normalized command only.
    No Telegram update objects or opaque payload blobs.
    """

    telegram_user_id: int
    correlation_id: str
    telegram_update_id: int | None
    normalized_command_text: str | None


class NormalizationRejectReason(str, Enum):
    """Safe, low-cardinality rejection categories for transport normalization."""

    UNKNOWN_COMMAND = "unknown_command"
    INVALID_INPUT = "invalid_input"
    MISSING_EVENT_ID_FOR_BOOTSTRAP = "missing_event_id_for_bootstrap"
    MISSING_EVENT_ID_FOR_RESEND = "missing_event_id_for_resend"


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Bootstrap:
    input: BootstrapIdentityInput


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Status:
    input: GetSubscriptionStatusInput


@dataclass(frozen=True, slots=True)
class NormalizedSlice1ResendAccess:
    input: TelegramAccessResendInput


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Help:
    """Read-only /help: correlation id for transport only; no application handler inputs."""

    correlation_id: str


@dataclass(frozen=True, slots=True)
class NormalizedSlice1Menu:
    """Read-only /menu: shows storefront main menu; no handler, no state change."""

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
    """Read-only /support: FAQ menu; no handler, no state change."""

    correlation_id: str


@dataclass(frozen=True, slots=True)
class NormalizedSlice1SupportContact:
    """Read-only /support_contact: safe contact lines; no handler, no state change."""

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
    | NormalizedSlice1Rejected
)


def normalize_command_token(raw: str | None) -> str | None:
    """
    Extract a bounded first-token command (e.g. /start, /start@bot → /start).
    Returns None if input is unusable; does not retain or echo full message bodies.
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


def parse_slice1_transport(envelope: TransportIncomingEnvelope) -> NormalizedSlice1Result:
    """Map allowlisted slice-1 commands to handler inputs; reject everything else safely."""
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
        return NormalizedSlice1Bootstrap(
            input=BootstrapIdentityInput(
                telegram_user_id=envelope.telegram_user_id,
                telegram_update_id=envelope.telegram_update_id,
                correlation_id=envelope.correlation_id,
            ),
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
