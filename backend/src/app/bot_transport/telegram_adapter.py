"""Thin Telegram-shaped → :class:`TransportIncomingEnvelope` extraction (slice 1; no SDK, no IO)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.bot_transport.normalized import TransportIncomingEnvelope
from app.security.validation import ValidationError, validate_telegram_update_id, validate_telegram_user_id
from app.shared.correlation import is_valid_correlation_id, new_correlation_id

_MAX_MESSAGE_TEXT_LEN = 512

_BUTTON_LABEL_TO_COMMAND: dict[str, str] = {
    "📊 тарифы": "/plans",
    "💳 купить": "/buy",
    "📱 подписка": "/my_subscription",
    "🔄 продлить": "/renew",
    "🆘 поддержка": "/support",
    "❓ помощь": "/help",
    "🔓 доступ": "/get_access",
    "📞 контакты": "/support_contact",
}

# Telegram update keys that are not plain private messages for slice 1.
_SLICE1_FORBIDDEN_UPDATE_KEYS: frozenset[str] = frozenset(
    {
        "inline_query",
        "chosen_inline_result",
        "shipping_query",
        "pre_checkout_query",
        "poll",
        "poll_answer",
        "my_chat_member",
        "chat_member",
        "chat_join_request",
        "edited_message",
        "channel_post",
        "edited_channel_post",
    },
)


class TelegramAdapterRejectReason(str, Enum):
    """Safe, low-cardinality reasons for adapter-level rejection (Telegram shape only)."""

    INVALID_UPDATE_CONTAINER = "invalid_update_container"
    UNSUPPORTED_UPDATE_SURFACE = "unsupported_update_surface"
    MISSING_MESSAGE = "missing_message"
    MISSING_UPDATE_ID = "missing_update_id"
    NON_PRIVATE_CHAT = "non_private_chat"
    MISSING_USER_ID = "missing_user_id"
    NON_TEXT_MESSAGE = "non_text_message"
    TEXT_TOO_LONG = "text_too_long"
    NOT_A_COMMAND = "not_a_command"
    INVALID_IDS = "invalid_ids"
    INVALID_CORRELATION_ID = "invalid_correlation_id"


@dataclass(frozen=True, slots=True)
class TelegramAdapterRejected:
    """Adapter could not produce a slice-1 envelope; carries a correlation id for tracing."""

    reason: TelegramAdapterRejectReason
    correlation_id: str


def _reject(reason: TelegramAdapterRejectReason, correlation_id: str) -> TelegramAdapterRejected:
    return TelegramAdapterRejected(reason=reason, correlation_id=correlation_id)


def _resolve_correlation(correlation_id: str | None) -> str | TelegramAdapterRejected:
    if correlation_id is None:
        return new_correlation_id()
    if not is_valid_correlation_id(correlation_id):
        return _reject(TelegramAdapterRejectReason.INVALID_CORRELATION_ID, new_correlation_id())
    return correlation_id


def _non_empty_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s if s else None


def extract_slice1_envelope_from_telegram_update(
    update: Mapping[str, Any],
    *,
    correlation_id: str | None = None,
) -> TransportIncomingEnvelope | TelegramAdapterRejected:
    """
    Map a raw Telegram-like update mapping to :class:`TransportIncomingEnvelope`.

    - Accepts only private ``message`` updates with text starting with ``/`` (command token).
    - Does not retain or forward the raw update; identifiers and bounded text only.
    - Unknown slash-commands (e.g. ``/foo``) are passed through as bounded text; the normalized
      layer applies the slice-1 allowlist.
    """
    cid_result = _resolve_correlation(correlation_id)
    if isinstance(cid_result, TelegramAdapterRejected):
        return cid_result
    cid = cid_result

    if not isinstance(update, Mapping):
        return _reject(TelegramAdapterRejectReason.INVALID_UPDATE_CONTAINER, cid)

    for key in _SLICE1_FORBIDDEN_UPDATE_KEYS:
        if key in update and update[key] is not None:
            return _reject(TelegramAdapterRejectReason.UNSUPPORTED_UPDATE_SURFACE, cid)

    raw_update_id = update.get("update_id")
    if raw_update_id is None:
        return _reject(TelegramAdapterRejectReason.MISSING_UPDATE_ID, cid)
    try:
        telegram_update_id = validate_telegram_update_id(raw_update_id)
    except ValidationError:
        return _reject(TelegramAdapterRejectReason.INVALID_IDS, cid)

    # Handle callback_query updates (inline button presses)
    if "callback_query" in update and update["callback_query"] is not None:
        return _extract_callback_envelope(update, cid=cid, telegram_update_id=telegram_update_id)

    message = update.get("message")
    if message is None:
        return _reject(TelegramAdapterRejectReason.MISSING_MESSAGE, cid)
    if not isinstance(message, Mapping):
        return _reject(TelegramAdapterRejectReason.MISSING_MESSAGE, cid)

    chat = message.get("chat")
    if not isinstance(chat, Mapping):
        return _reject(TelegramAdapterRejectReason.NON_PRIVATE_CHAT, cid)
    if chat.get("type") != "private":
        return _reject(TelegramAdapterRejectReason.NON_PRIVATE_CHAT, cid)

    from_user = message.get("from")
    if not isinstance(from_user, Mapping):
        return _reject(TelegramAdapterRejectReason.MISSING_USER_ID, cid)
    raw_user_id = from_user.get("id")
    try:
        telegram_user_id = validate_telegram_user_id(raw_user_id)
    except ValidationError:
        return _reject(TelegramAdapterRejectReason.INVALID_IDS, cid)

    text = _non_empty_str(message.get("text"))
    if text is None:
        return _reject(TelegramAdapterRejectReason.NON_TEXT_MESSAGE, cid)
    if len(text) > _MAX_MESSAGE_TEXT_LEN:
        return _reject(TelegramAdapterRejectReason.TEXT_TOO_LONG, cid)
    if not text.startswith("/"):
        mapped = _BUTTON_LABEL_TO_COMMAND.get(text.lower())
        if mapped is None:
            return _reject(TelegramAdapterRejectReason.NOT_A_COMMAND, cid)
        text = mapped

    return TransportIncomingEnvelope(
        telegram_user_id=telegram_user_id,
        correlation_id=cid,
        telegram_update_id=telegram_update_id,
        normalized_command_text=text,
    )


def _extract_callback_envelope(
    update: Mapping[str, Any],
    *,
    cid: str,
    telegram_update_id: int,
) -> TransportIncomingEnvelope | TelegramAdapterRejected:
    """Extract envelope from a callback_query update."""
    cq = update.get("callback_query")
    if not isinstance(cq, Mapping):
        return _reject(TelegramAdapterRejectReason.UNSUPPORTED_UPDATE_SURFACE, cid)

    from_user = cq.get("from")
    if not isinstance(from_user, Mapping):
        return _reject(TelegramAdapterRejectReason.MISSING_USER_ID, cid)
    try:
        telegram_user_id = validate_telegram_user_id(from_user.get("id"))
    except ValidationError:
        return _reject(TelegramAdapterRejectReason.INVALID_IDS, cid)

    callback_data = _non_empty_str(cq.get("data"))
    if callback_data is None:
        return _reject(TelegramAdapterRejectReason.NON_TEXT_MESSAGE, cid)

    return TransportIncomingEnvelope(
        telegram_user_id=telegram_user_id,
        correlation_id=cid,
        telegram_update_id=telegram_update_id,
        normalized_command_text=None,
        callback_data=callback_data,
    )
