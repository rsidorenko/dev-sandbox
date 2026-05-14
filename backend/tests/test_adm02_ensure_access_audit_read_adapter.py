"""Tests for PostgreSQL ADM-02 ensure-access audit read adapter."""

from __future__ import annotations

import asyncpg
import pytest

from app.admin_support.adm02_postgres_ensure_access_audit_read_adapter import (
    Adm02PostgresEnsureAccessAuditReadAdapter,
)
from app.admin_support.contracts import Adm02EnsureAccessAuditReadQuery
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


def test_read_by_correlation_id_uses_bounded_query_and_maps_safe_fields() -> None:
    class _Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def fetch(self, query: str, *params: object):
            self.calls.append((query, params))
            return [
                {
                    "created_at": "2026-04-26 00:00:00+00",
                    "event_type": "ensure_access",
                    "outcome_bucket": "issued_access",
                    "remediation_result": "issued_access",
                    "readiness_bucket": "active_access_ready",
                    "principal_marker": "internal_admin_redacted",
                    "correlation_id": "a" * 32,
                    "source_marker": "internal_admin_runtime",
                }
            ]

    conn = _Conn()
    adapter = Adm02PostgresEnsureAccessAuditReadAdapter(_Pool(conn))
    result = _run(
        adapter.read_ensure_access_audit_evidence(Adm02EnsureAccessAuditReadQuery(correlation_id="a" * 32, limit=7))
    )
    assert len(result.items) == 1
    item = result.items[0]
    assert item.outcome_bucket.value == "issued_access"
    assert item.remediation_result is not None and item.remediation_result.value == "issued_access"
    assert item.readiness_bucket is not None and item.readiness_bucket.value == "active_access_ready"
    assert conn.calls[0][1] == ("a" * 32, 7)
    blob = str(item).lower()
    for forbidden in _FORBIDDEN:
        assert forbidden not in blob


def test_recent_query_uses_default_limit_when_non_positive() -> None:
    class _Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def fetch(self, query: str, *params: object):
            self.calls.append((query, params))
            return []

    conn = _Conn()
    adapter = Adm02PostgresEnsureAccessAuditReadAdapter(_Pool(conn))
    _run(adapter.read_ensure_access_audit_evidence(Adm02EnsureAccessAuditReadQuery(correlation_id=None, limit=0)))
    assert conn.calls[0][1] == (20,)


def test_recent_query_clamps_to_hard_max_limit() -> None:
    class _Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def fetch(self, query: str, *params: object):
            self.calls.append((query, params))
            return []

    conn = _Conn()
    adapter = Adm02PostgresEnsureAccessAuditReadAdapter(_Pool(conn))
    _run(adapter.read_ensure_access_audit_evidence(Adm02EnsureAccessAuditReadQuery(correlation_id=None, limit=500)))
    assert conn.calls[0][1] == (100,)


def test_database_failure_wrapped_without_sensitive_leak() -> None:
    class _Conn:
        async def fetch(self, query: str, *params: object):
            _ = (query, params)
            raise asyncpg.PostgresError("postgresql://sensitive")

    adapter = Adm02PostgresEnsureAccessAuditReadAdapter(_Pool(_Conn()))
    with pytest.raises(PersistenceDependencyError) as exc:
        _run(adapter.read_ensure_access_audit_evidence(Adm02EnsureAccessAuditReadQuery(correlation_id=None, limit=5)))
    lowered = str(exc.value).lower()
    for forbidden in _FORBIDDEN:
        assert forbidden not in lowered
