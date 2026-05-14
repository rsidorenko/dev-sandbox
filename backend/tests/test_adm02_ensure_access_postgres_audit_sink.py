"""Tests for durable PostgreSQL ADM-02 ensure-access audit sink."""

from __future__ import annotations

import asyncpg
import pytest

from app.admin_support.adm02_ensure_access_audit_postgres import PostgresAdm02EnsureAccessAuditSink
from app.admin_support.contracts import (
    Adm01SupportAccessReadinessBucket,
    Adm02EnsureAccessAuditEvent,
    Adm02EnsureAccessAuditEventType,
    Adm02EnsureAccessAuditOutcomeBucket,
    Adm02EnsureAccessAuditPrincipalMarker,
    Adm02EnsureAccessRemediationResult,
)
from app.security.errors import PersistenceDependencyError
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


class _AcquireCtx:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn) -> None:
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


def _event() -> Adm02EnsureAccessAuditEvent:
    return Adm02EnsureAccessAuditEvent(
        event_type=Adm02EnsureAccessAuditEventType.ENSURE_ACCESS,
        outcome_bucket=Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS,
        remediation_result=Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
        readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
        principal_marker=Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED,
        correlation_id="a" * 32,
    )


def test_sink_persists_only_safe_bounded_fields() -> None:
    class _Conn:
        def __init__(self) -> None:
            self.query = ""
            self.params: tuple[object, ...] = ()

        async def execute(self, query: str, *params: object):
            self.query = query
            self.params = params
            return "INSERT 0 1"

    conn = _Conn()
    sink = PostgresAdm02EnsureAccessAuditSink(_Pool(conn), source_marker="runtime")
    _run(sink.append_ensure_access_event(_event()))

    assert "adm02_ensure_access_audit_events" in conn.query
    assert len(conn.params) == 8
    assert conn.params[1:] == (
        "ensure_access",
        "issued_access",
        "issued_access",
        "active_access_ready",
        "internal_admin_redacted",
        "a" * 32,
        "runtime",
    )
    lowered = "|".join("" if p is None else str(p) for p in conn.params).lower()
    for forbidden in _FORBIDDEN:
        assert forbidden not in lowered


def test_sink_wraps_database_failures_without_leaking_details() -> None:
    class _Conn:
        async def execute(self, query: str, *params: object):
            _ = (query, params)
            raise asyncpg.PostgresError("postgresql://sensitive")

    sink = PostgresAdm02EnsureAccessAuditSink(_Pool(_Conn()))
    with pytest.raises(PersistenceDependencyError) as exc:
        _run(sink.append_ensure_access_event(_event()))
    lowered = str(exc.value).lower()
    for forbidden in _FORBIDDEN:
        assert forbidden not in lowered
