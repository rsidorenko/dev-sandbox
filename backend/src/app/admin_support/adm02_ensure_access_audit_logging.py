"""Structured redacted logging sink for ADM-02 ensure-access audit events."""

from __future__ import annotations

import logging

from app.admin_support.contracts import Adm02EnsureAccessAuditEvent, Adm02EnsureAccessAuditPort

_LOGGER = logging.getLogger(__name__)


class StructuredLoggingAdm02EnsureAccessAuditSink(Adm02EnsureAccessAuditPort):
    """Emit bounded redacted ADM-02 ensure-access audit events to structured logs."""

    async def append_ensure_access_event(self, event: Adm02EnsureAccessAuditEvent) -> None:
        remediation_result = None
        if event.remediation_result is not None:
            remediation_result = event.remediation_result.value
        readiness_bucket = None
        if event.readiness_bucket is not None:
            readiness_bucket = event.readiness_bucket.value
        _LOGGER.info(
            "admin_support.adm02.ensure_access.audit",
            extra={
                "structured_fields": {
                    "event_type": event.event_type.value,
                    "outcome_bucket": event.outcome_bucket.value,
                    "remediation_result": remediation_result,
                    "readiness_bucket": readiness_bucket,
                    "principal_marker": event.principal_marker.value,
                    "correlation_id": event.correlation_id,
                }
            },
        )


class FanoutAdm02EnsureAccessAuditSink(Adm02EnsureAccessAuditPort):
    """Best-effort fanout sink; suppresses per-sink failures to stay degrade-safe."""

    def __init__(self, *sinks: Adm02EnsureAccessAuditPort) -> None:
        self._sinks = tuple(sinks)

    async def append_ensure_access_event(self, event: Adm02EnsureAccessAuditEvent) -> None:
        for sink in self._sinks:
            try:
                await sink.append_ensure_access_event(event)
            except Exception:  # noqa: S112 — best-effort fan-out, one sink failure must not block others
                continue
