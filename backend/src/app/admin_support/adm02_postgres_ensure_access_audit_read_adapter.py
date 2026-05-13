"""PostgreSQL read-only adapter for durable ADM-02 ensure-access audit evidence."""

from __future__ import annotations

import asyncpg

from app.admin_support.contracts import (
    Adm01SupportAccessReadinessBucket,
    Adm02EnsureAccessAuditEventType,
    Adm02EnsureAccessAuditEvidenceItem,
    Adm02EnsureAccessAuditOutcomeBucket,
    Adm02EnsureAccessAuditPrincipalMarker,
    Adm02EnsureAccessAuditReadPort,
    Adm02EnsureAccessAuditReadQuery,
    Adm02EnsureAccessAuditReadResult,
    Adm02EnsureAccessRemediationResult,
)
from app.security.errors import InternalErrorCategory, PersistenceDependencyError

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


class Adm02PostgresEnsureAccessAuditReadAdapter(Adm02EnsureAccessAuditReadPort):
    _SELECT_BY_CORRELATION = """
        SELECT
            created_at::text AS created_at,
            event_type,
            outcome_bucket,
            remediation_result,
            readiness_bucket,
            principal_marker,
            correlation_id,
            source_marker
        FROM adm02_ensure_access_audit_events
        WHERE correlation_id = $1::text
        ORDER BY created_at DESC, audit_event_id DESC
        LIMIT $2::int
    """
    _SELECT_RECENT = """
        SELECT
            created_at::text AS created_at,
            event_type,
            outcome_bucket,
            remediation_result,
            readiness_bucket,
            principal_marker,
            correlation_id,
            source_marker
        FROM adm02_ensure_access_audit_events
        ORDER BY created_at DESC, audit_event_id DESC
        LIMIT $1::int
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def read_ensure_access_audit_evidence(
        self,
        query: Adm02EnsureAccessAuditReadQuery,
    ) -> Adm02EnsureAccessAuditReadResult:
        limit = max(1, min(query.limit if query.limit > 0 else _DEFAULT_LIMIT, _MAX_LIMIT))
        try:
            async with self._pool.acquire() as conn:
                if query.correlation_id is not None:
                    rows = await conn.fetch(self._SELECT_BY_CORRELATION, query.correlation_id, limit)
                else:
                    rows = await conn.fetch(self._SELECT_RECENT, limit)
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc
        return Adm02EnsureAccessAuditReadResult(
            items=tuple(self._map_row(row) for row in rows),
        )

    @staticmethod
    def _map_row(row: asyncpg.Record) -> Adm02EnsureAccessAuditEvidenceItem:
        remediation_raw = row["remediation_result"]
        readiness_raw = row["readiness_bucket"]
        source_raw = row["source_marker"]
        return Adm02EnsureAccessAuditEvidenceItem(
            created_at=str(row["created_at"]),
            event_type=Adm02EnsureAccessAuditEventType(str(row["event_type"])),
            outcome_bucket=Adm02EnsureAccessAuditOutcomeBucket(str(row["outcome_bucket"])),
            remediation_result=(
                Adm02EnsureAccessRemediationResult(str(remediation_raw)) if remediation_raw is not None else None
            ),
            readiness_bucket=(
                Adm01SupportAccessReadinessBucket(str(readiness_raw)) if readiness_raw is not None else None
            ),
            principal_marker=Adm02EnsureAccessAuditPrincipalMarker(str(row["principal_marker"])),
            correlation_id=str(row["correlation_id"]),
            source_marker=None if source_raw is None else str(source_raw),
        )
