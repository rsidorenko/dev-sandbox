"""Tests for structured ADM-02 ensure-access audit logging sink."""

from __future__ import annotations

import logging

from app.admin_support.adm02_ensure_access_audit_logging import (
    FanoutAdm02EnsureAccessAuditSink,
    StructuredLoggingAdm02EnsureAccessAuditSink,
)
from app.admin_support.contracts import (
    Adm01SupportAccessReadinessBucket,
    Adm02EnsureAccessAuditEvent,
    Adm02EnsureAccessAuditEventType,
    Adm02EnsureAccessAuditOutcomeBucket,
    Adm02EnsureAccessAuditPrincipalMarker,
    Adm02EnsureAccessRemediationResult,
)
from app.shared.test_helpers import run_async as _run

_FORBIDDEN = (
    "database_url",
    "postgres://",
    "postgresql://",
    "bearer ",
    "private key",
    "begin ",
    "token=",
    "vpn://",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "schema_version",
    "customer_ref",
    "provider_ref",
    "checkout_attempt_id",
    "internal_user_id",
)


def test_sink_emits_only_bounded_safe_fields(monkeypatch) -> None:
    sink = StructuredLoggingAdm02EnsureAccessAuditSink()
    recorded: list[dict] = []

    def fake_info(_msg: str, *, extra: dict) -> None:
        recorded.append(extra["structured_fields"])

    monkeypatch.setattr(logging.getLogger(sink.__module__), "info", fake_info)
    event = Adm02EnsureAccessAuditEvent(
        event_type=Adm02EnsureAccessAuditEventType.ENSURE_ACCESS,
        outcome_bucket=Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS,
        remediation_result=Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
        readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
        principal_marker=Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED,
        correlation_id="a" * 32,
    )
    _run(sink.append_ensure_access_event(event))
    assert len(recorded) == 1
    payload = recorded[0]
    assert set(payload.keys()) == {
        "event_type",
        "outcome_bucket",
        "remediation_result",
        "readiness_bucket",
        "principal_marker",
        "correlation_id",
    }
    lowered = str(payload).lower()
    for forbidden in _FORBIDDEN:
        assert forbidden not in lowered


def test_sink_omits_raw_principal_or_user_identifiers(monkeypatch) -> None:
    sink = StructuredLoggingAdm02EnsureAccessAuditSink()
    captured: list[dict] = []

    def fake_info(_msg: str, *, extra: dict) -> None:
        captured.append(extra["structured_fields"])

    monkeypatch.setattr(logging.getLogger(sink.__module__), "info", fake_info)
    event = Adm02EnsureAccessAuditEvent(
        event_type=Adm02EnsureAccessAuditEventType.ENSURE_ACCESS,
        outcome_bucket=Adm02EnsureAccessAuditOutcomeBucket.NOOP_ACCESS_ALREADY_READY,
        remediation_result=Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY,
        readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
        principal_marker=Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED,
        correlation_id="b" * 32,
    )
    _run(sink.append_ensure_access_event(event))
    payload = captured[0]
    blob = str(payload).lower()
    assert "adm-" not in blob
    assert "telegram" not in blob
    assert "internal_user_id" not in blob


def test_fanout_sink_forwards_to_all_children() -> None:
    recorded: list[str] = []

    class _Sink:
        async def append_ensure_access_event(self, event: Adm02EnsureAccessAuditEvent) -> None:
            _ = event
            recorded.append("ok")

    event = Adm02EnsureAccessAuditEvent(
        event_type=Adm02EnsureAccessAuditEventType.ENSURE_ACCESS,
        outcome_bucket=Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS,
        remediation_result=Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
        readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
        principal_marker=Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED,
        correlation_id="c" * 32,
    )
    sink = FanoutAdm02EnsureAccessAuditSink(_Sink(), _Sink())
    _run(sink.append_ensure_access_event(event))
    assert recorded == ["ok", "ok"]


def test_fanout_sink_swallows_child_failures_and_keeps_other_sinks() -> None:
    recorded: list[str] = []

    class _FailingSink:
        async def append_ensure_access_event(self, event: Adm02EnsureAccessAuditEvent) -> None:
            _ = event
            raise RuntimeError("postgresql://should-not-leak")

    class _OkSink:
        async def append_ensure_access_event(self, event: Adm02EnsureAccessAuditEvent) -> None:
            _ = event
            recorded.append("ok")

    event = Adm02EnsureAccessAuditEvent(
        event_type=Adm02EnsureAccessAuditEventType.ENSURE_ACCESS,
        outcome_bucket=Adm02EnsureAccessAuditOutcomeBucket.NOOP_ACCESS_ALREADY_READY,
        remediation_result=Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY,
        readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
        principal_marker=Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED,
        correlation_id="d" * 32,
    )
    sink = FanoutAdm02EnsureAccessAuditSink(_FailingSink(), _OkSink())
    _run(sink.append_ensure_access_event(event))
    assert recorded == ["ok"]
