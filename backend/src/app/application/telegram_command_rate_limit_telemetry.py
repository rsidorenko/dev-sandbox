"""Безопасные/редуцированные события наблюдаемости для решений rate-limit команд Telegram."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from app.application.telegram_command_rate_limit import TelegramCommandRateLimitKey

_LOGGER = logging.getLogger(__name__)

TelegramRateLimitDecision = Literal["allowed", "limited"]
TelegramRateLimitCommandBucket = Literal["status", "access_resend", "support", "other"]
TelegramRateLimitPrincipalMarker = Literal["telegram_user_redacted"]
TelegramRateLimitUpdateMarker = Literal["present", "absent"]
TelegramRateLimitWindowBucket = Literal[
    "status_window",
    "access_resend_window",
    "support_window",
    "other_window",
]


@dataclass(frozen=True, slots=True)
class TelegramCommandRateLimitDecisionEvent:
    event_type: Literal["telegram_command_rate_limit_decision"]
    command_bucket: TelegramRateLimitCommandBucket
    decision: TelegramRateLimitDecision
    limit_window_bucket: TelegramRateLimitWindowBucket
    principal_marker: TelegramRateLimitPrincipalMarker
    correlation_id: str
    update_marker: TelegramRateLimitUpdateMarker


class TelegramCommandRateLimitTelemetry(Protocol):
    async def emit_decision(self, event: TelegramCommandRateLimitDecisionEvent) -> None: ...


def command_bucket_from_key(key: TelegramCommandRateLimitKey) -> TelegramRateLimitCommandBucket:
    if key is TelegramCommandRateLimitKey.STATUS:
        return "status"
    if key is TelegramCommandRateLimitKey.ACCESS_RESEND:
        return "access_resend"
    if key is TelegramCommandRateLimitKey.SUPPORT:
        return "support"
    return "other"


def window_bucket_from_key(key: TelegramCommandRateLimitKey) -> TelegramRateLimitWindowBucket:
    if key is TelegramCommandRateLimitKey.STATUS:
        return "status_window"
    if key is TelegramCommandRateLimitKey.ACCESS_RESEND:
        return "access_resend_window"
    if key is TelegramCommandRateLimitKey.SUPPORT:
        return "support_window"
    return "other_window"


class NoopTelegramCommandRateLimitTelemetry:
    async def emit_decision(self, event: TelegramCommandRateLimitDecisionEvent) -> None:
        _ = event


class StructuredLoggingTelegramCommandRateLimitTelemetry:
    """Отправляет ограниченную, редуцированную телеметрию для решений rate-limit команд."""

    async def emit_decision(self, event: TelegramCommandRateLimitDecisionEvent) -> None:
        try:
            _LOGGER.info(
                "bot_transport.telegram.command_rate_limit.decision",
                extra={
                    "structured_fields": {
                        "event_type": event.event_type,
                        "command_bucket": event.command_bucket,
                        "decision": event.decision,
                        "limit_window_bucket": event.limit_window_bucket,
                        "principal_marker": event.principal_marker,
                        "correlation_id": event.correlation_id,
                        "update_marker": event.update_marker,
                    }
                },
            )
        except Exception:
            _LOGGER.debug(
                "bot_transport.telegram.command_rate_limit.telemetry_dropped",
                exc_info=True,
            )
