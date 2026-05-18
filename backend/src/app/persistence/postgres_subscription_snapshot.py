"""PostgreSQL adapter for subscription snapshot read + insert-if-missing (asyncpg pool)."""

from __future__ import annotations

import asyncpg

from app.application.interfaces import SubscriptionSnapshot
from app.security.errors import InternalErrorCategory, PersistenceDependencyError


class PostgresSubscriptionSnapshotReader:
    """One row per internal user; read returns ``None`` when missing; ``put_if_absent`` is no-op on conflict."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_for_user(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT internal_user_id, state_label, active_until_utc, plan_id, device_count,
                           trial_started_at, trial_expires_at
                    FROM subscription_snapshots
                    WHERE internal_user_id = $1::text
                    """,
                    internal_user_id,
                )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc
        if row is None:
            return None
        return SubscriptionSnapshot(
            internal_user_id=row["internal_user_id"],
            state_label=row["state_label"],
            active_until_utc=row["active_until_utc"],
            plan_id=row.get("plan_id"),
            device_count=row.get("device_count"),
            trial_started_at=row.get("trial_started_at"),
            trial_expires_at=row.get("trial_expires_at"),
        )

    async def put_if_absent(self, snapshot: SubscriptionSnapshot) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO subscription_snapshots (internal_user_id, state_label, active_until_utc, plan_id, device_count)
                    VALUES ($1::text, $2::text, $3::timestamptz, $4::text, COALESCE($5, 5))
                    ON CONFLICT (internal_user_id) DO NOTHING
                    """,
                    snapshot.internal_user_id,
                    snapshot.state_label,
                    snapshot.active_until_utc,
                    snapshot.plan_id,
                    snapshot.device_count,
                )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc

    _UPSERT_STATE = """
        INSERT INTO subscription_snapshots (internal_user_id, state_label, active_until_utc, plan_id, device_count, trial_started_at, trial_expires_at)
        VALUES ($1::text, $2::text, $3::timestamptz, $4::text, COALESCE($5, 5), $6::timestamptz, $7::timestamptz)
        ON CONFLICT (internal_user_id) DO UPDATE
        SET state_label = EXCLUDED.state_label,
            active_until_utc = EXCLUDED.active_until_utc,
            plan_id = EXCLUDED.plan_id,
            device_count = COALESCE(EXCLUDED.device_count, subscription_snapshots.device_count),
            trial_started_at = COALESCE(EXCLUDED.trial_started_at, subscription_snapshots.trial_started_at),
            trial_expires_at = COALESCE(EXCLUDED.trial_expires_at, subscription_snapshots.trial_expires_at),
            updated_at = now()
    """

    @staticmethod
    async def upsert_state_in_connection(
        conn: asyncpg.Connection,
        snapshot: SubscriptionSnapshot,
    ) -> None:
        try:
            await conn.execute(
                PostgresSubscriptionSnapshotReader._UPSERT_STATE,
                snapshot.internal_user_id,
                snapshot.state_label,
                snapshot.active_until_utc,
                snapshot.plan_id,
                snapshot.device_count,
                snapshot.trial_started_at,
                snapshot.trial_expires_at,
            )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc

    async def upsert_state(self, snapshot: SubscriptionSnapshot) -> None:
        try:
            async with self._pool.acquire() as conn:
                await PostgresSubscriptionSnapshotReader.upsert_state_in_connection(conn, snapshot)
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc
