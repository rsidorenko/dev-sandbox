"""Safe/redacted security telemetry for Telegram webhook ingress decisions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

_LOGGER = logging.getLogger(__name__)

TelegramWebhookIngressDecision = Literal[
    "accepted",
    "duplicate_ignored",
    "unauthorized",
    "invalid_json",
    "invalid_update",
    "disabled",
    "not_ready",
]
TelegramWebhookIngressReasonBucket = Literal[
    "valid_secret",
    "duplicate_update",
    "missing_secret_header",
    "invalid_secret_header",
    "invalid_json",
    "missing_update_id",
    "invalid_update_id",
    "webhook_disabled",
    "readiness_failed",
]
TelegramWebhookIngressPathBucket = Literal["telegram_webhook", "healthz", "readyz", "other"]
TelegramWebhookIngressPrincipalMarker = Literal["telegram_webhook_redacted"]


@dataclass(frozen=True, slots=True)
class TelegramWebhookIngressDecisionEvent:
    event_type: Literal["telegram_webhook_ingress_decision"]
    decision: TelegramWebhookIngressDecision
    reason_bucket: TelegramWebhookIngressReasonBucket
    path_bucket: TelegramWebhookIngressPathBucket
    principal_marker: TelegramWebhookIngressPrincipalMarker
    correlation_id: str | None = None


class TelegramWebhookIngressTelemetry(Protocol):
    async def emit_decision(self, event: TelegramWebhookIngressDecisionEvent) -> None: ...


class NoopTelegramWebhookIngressTelemetry:
    async def emit_decision(self, event: TelegramWebhookIngressDecisionEvent) -> None:
        _ = event


class StructuredLoggingTelegramWebhookIngressTelemetry:
    """Emit bounded/redacted ingress decision events to structured logs."""

    async def emit_decision(self, event: TelegramWebhookIngressDecisionEvent) -> None:
        try:
            _LOGGER.info(
                "runtime.telegram_webhook_ingress.security_decision",
                extra={
                    "structured_fields": {
                        "event_type": event.event_type,
                        "decision": event.decision,
                        "reason_bucket": event.reason_bucket,
                        "path_bucket": event.path_bucket,
                        "principal_marker": event.principal_marker,
                        "correlation_id": event.correlation_id,
                    }
                },
            )
        except Exception:
            _LOGGER.debug(
                "runtime.telegram_webhook_ingress.telemetry_dropped",
                exc_info=True,
            )


class FanoutTelegramWebhookIngressTelemetry:
    """Best-effort fanout sink; suppresses per-sink failures."""

    def __init__(self, *sinks: TelegramWebhookIngressTelemetry) -> None:
        self._sinks = tuple(sinks)

    async def emit_decision(self, event: TelegramWebhookIngressDecisionEvent) -> None:
        for sink in self._sinks:
            try:
                await sink.emit_decision(event)
            except Exception:  # noqa: S112 — best-effort fan-out, one sink failure must not block others
                continue
