"""PostgreSQL store for opaque issuance handles and issued/revoked state (no secret payloads)."""

from __future__ import annotations

from datetime import datetime

import asyncpg

from app.persistence.issuance_state_record import IssuanceStatePersistence, IssuanceStateRow
from app.security.errors import InternalErrorCategory, PersistenceDependencyError

# Reject ref strings that look like config/secrets; aligned with issuance unit tests.
_FORBIDDEN_REF_SUBSTRINGS = (
    "PRIVATE KEY",
    "BEGIN ",
    "token=",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "vpn://",
)


def _assert_non_secret_provider_ref(value: str) -> None:
    u = value.upper()
    for frag in _FORBIDDEN_REF_SUBSTRINGS:
        if frag.upper() in u:
            msg = "provider_issuance_ref must be an opaque non-secret ref"
            raise ValueError(msg)


def _row_to_domain(row: asyncpg.Record) -> IssuanceStateRow:
    st = str(row["issuance_state"])
    return IssuanceStateRow(
        internal_user_id=str(row["internal_user_id"]),
        issue_idempotency_key=str(row["issue_idempotency_key"]),
        state=IssuanceStatePersistence(st),
        provider_issuance_ref=str(row["provider_issuance_ref"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        revoked_at=row["revoked_at"],
    )


class PostgresIssuanceStateRepository:
    """
    Durable operational state for config issuance (slice 1).

    Idempotency: ``issue_or_get`` never overwrites an existing
    ``(internal_user_id, issue_idempotency_key)`` row. ``mark_revoked`` is safe to repeat;
    a missing row returns ``None`` (no issuance to revoke at rest).
    """

    _INSERT_ISSUE = """
        INSERT INTO issuance_state (
            internal_user_id,
            issue_idempotency_key,
            issuance_state,
            provider_issuance_ref,
            created_at,
            updated_at,
            revoked_at
        )
        VALUES ($1::text, $2::text, 'issued', $3::text, now(), now(), NULL)
        ON CONFLICT (internal_user_id, issue_idempotency_key) DO NOTHING
        RETURNING
            internal_user_id,
            issue_idempotency_key,
            issuance_state,
            provider_issuance_ref,
            created_at,
            updated_at,
            revoked_at
    """

    _SELECT_BY_KEYS = """
        SELECT
            internal_user_id,
            issue_idempotency_key,
            issuance_state,
            provider_issuance_ref,
            created_at,
            updated_at,
            revoked_at
        FROM issuance_state
        WHERE internal_user_id = $1::text
          AND issue_idempotency_key = $2::text
    """

    _UPDATE_REVOKE = """
        UPDATE issuance_state
        SET
            issuance_state = 'revoked',
            revoked_at = COALESCE(revoked_at, now()),
            updated_at = now()
        WHERE internal_user_id = $1::text
          AND issue_idempotency_key = $2::text
          AND issuance_state = 'issued'
        RETURNING
            internal_user_id,
            issue_idempotency_key,
            issuance_state,
            provider_issuance_ref,
            created_at,
            updated_at,
            revoked_at
    """

    _SELECT_CURRENT = """
        SELECT
            internal_user_id,
            issue_idempotency_key,
            issuance_state,
            provider_issuance_ref,
            created_at,
            updated_at,
            revoked_at
        FROM issuance_state
        WHERE internal_user_id = $1::text
        ORDER BY updated_at DESC, issue_idempotency_key DESC
        LIMIT 1
    """

    _RECONCILE_EXPIRED_SUBSCRIPTIONS = """
        UPDATE issuance_state AS i
        SET
            issuance_state = 'revoked',
            revoked_at = COALESCE(i.revoked_at, $1::timestamptz),
            updated_at = $1::timestamptz
        WHERE i.issuance_state = 'issued'
          AND EXISTS (
              SELECT 1
              FROM subscription_snapshots AS s
              WHERE s.internal_user_id = i.internal_user_id
                AND s.state_label = 'active'
                AND s.active_until_utc IS NOT NULL
                AND s.active_until_utc < $1::timestamptz
          )
    """

    _INSERT_ACCESS_RECONCILE_RUN_STARTED = """
        INSERT INTO access_reconcile_runs (
            run_id,
            task_name,
            started_at,
            status,
            reconciled_rows,
            error_class,
            error_message
        )
        VALUES ($1::uuid, $2::text, $3::timestamptz, 'started', 0, NULL, NULL)
    """

    _UPDATE_ACCESS_RECONCILE_RUN_COMPLETED = """
        UPDATE access_reconcile_runs
        SET
            finished_at = $3::timestamptz,
            status = 'completed',
            reconciled_rows = $4::int,
            error_class = NULL,
            error_message = NULL
        WHERE run_id = $1::uuid
          AND task_name = $2::text
    """

    _UPDATE_ACCESS_RECONCILE_RUN_FAILED = """
        UPDATE access_reconcile_runs
        SET
            finished_at = $3::timestamptz,
            status = 'failed',
            error_class = $4::text,
            error_message = $5::text
        WHERE run_id = $1::uuid
          AND task_name = $2::text
    """

    _SELECT_LATEST_ACCESS_RECONCILE_RUN = """
        SELECT
            started_at,
            finished_at,
            status,
            reconciled_rows,
            error_class,
            error_message
        FROM access_reconcile_runs
        WHERE task_name = $1::text
        ORDER BY started_at DESC, run_id DESC
        LIMIT 1
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def fetch_by_issue_keys(
        self, *, internal_user_id: str, issue_idempotency_key: str
    ) -> IssuanceStateRow | None:
        """Read-only lookup for idempotent issue / revoke without mutating state."""
        try:
            async with self._pool.acquire() as conn:
                cur = await conn.fetchrow(
                    self._SELECT_BY_KEYS,
                    internal_user_id,
                    issue_idempotency_key,
                )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc
        if cur is None:
            return None
        return _row_to_domain(cur)

    async def issue_or_get(
        self,
        *,
        internal_user_id: str,
        issue_idempotency_key: str,
        provider_issuance_ref: str,
    ) -> IssuanceStateRow:
        _assert_non_secret_provider_ref(provider_issuance_ref)
        try:
            async with self._pool.acquire() as conn:
                ins = await conn.fetchrow(
                    self._INSERT_ISSUE,
                    internal_user_id,
                    issue_idempotency_key,
                    provider_issuance_ref,
                )
                if ins is not None:
                    return _row_to_domain(ins)
                cur = await conn.fetchrow(
                    self._SELECT_BY_KEYS,
                    internal_user_id,
                    issue_idempotency_key,
                )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc
        if cur is None:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_INVARIANT)
        return _row_to_domain(cur)

    async def mark_revoked(self, *, internal_user_id: str, issue_idempotency_key: str) -> IssuanceStateRow | None:
        try:
            async with self._pool.acquire() as conn:
                upd = await conn.fetchrow(self._UPDATE_REVOKE, internal_user_id, issue_idempotency_key)
                if upd is not None:
                    return _row_to_domain(upd)
                cur = await conn.fetchrow(
                    self._SELECT_BY_KEYS,
                    internal_user_id,
                    issue_idempotency_key,
                )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc
        if cur is None:
            return None
        return _row_to_domain(cur)

    async def get_current_for_user(self, internal_user_id: str) -> IssuanceStateRow | None:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(self._SELECT_CURRENT, internal_user_id)
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc
        if row is None:
            return None
        return _row_to_domain(row)

    async def reconcile_expired_active_subscriptions(self, *, now_utc) -> int:
        try:
            async with self._pool.acquire() as conn:
                result = await conn.execute(
                    self._RECONCILE_EXPIRED_SUBSCRIPTIONS,
                    now_utc,
                )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc
        parts = result.split()
        if len(parts) == 2 and parts[0] == "UPDATE":
            try:
                return int(parts[1])
            except ValueError:
                return 0
        return 0

    async def record_access_reconcile_started(
        self,
        *,
        run_id: str,
        task_name: str,
        started_at,
    ) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    self._INSERT_ACCESS_RECONCILE_RUN_STARTED,
                    run_id,
                    task_name,
                    started_at,
                )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc

    async def record_access_reconcile_completed(
        self,
        *,
        run_id: str,
        task_name: str,
        finished_at,
        reconciled_rows: int,
    ) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    self._UPDATE_ACCESS_RECONCILE_RUN_COMPLETED,
                    run_id,
                    task_name,
                    finished_at,
                    reconciled_rows,
                )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc

    async def record_access_reconcile_failed(
        self,
        *,
        run_id: str,
        task_name: str,
        finished_at,
        safe_error_class: str,
        safe_error_message: str,
    ) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    self._UPDATE_ACCESS_RECONCILE_RUN_FAILED,
                    run_id,
                    task_name,
                    finished_at,
                    safe_error_class,
                    safe_error_message,
                )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc

    async def fetch_latest_access_reconcile_run(
        self,
        *,
        task_name: str,
    ) -> tuple[datetime, datetime | None, str, int, str | None, str | None] | None:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(self._SELECT_LATEST_ACCESS_RECONCILE_RUN, task_name)
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc
        if row is None:
            return None
        return (
            row["started_at"],
            row["finished_at"],
            str(row["status"]),
            int(row["reconciled_rows"]),
            None if row["error_class"] is None else str(row["error_class"]),
            None if row["error_message"] is None else str(row["error_message"]),
        )
