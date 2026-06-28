"""PostgreSQL adapter for UserIdentityRepository (asyncpg pool injected by composition/tests)."""

from __future__ import annotations

import asyncpg

from app.application.interfaces import IdentityRecord
from app.security.errors import InternalErrorCategory, PersistenceDependencyError


def internal_user_id_for_telegram(telegram_user_id: int) -> str:
    """Matches InMemoryUserIdentityRepository mapping semantics."""
    return f"u{telegram_user_id}"


def telegram_user_id_from_internal(internal_user_id: str) -> int | None:
    """Inverse of internal_user_id_for_telegram: parse the telegram id out of `u{telegram_user_id}`.

    Returns None for malformed input. NB web users carry a NEGATIVE telegram id (u-12345 → -12345);
    callers MUST skip non-positive ids — those accounts have no Telegram chat to deliver to. This
    is a documented invariant: keep in sync with internal_user_id_for_telegram. Pure + unit-tested.
    """
    if (
        not isinstance(internal_user_id, str)
        or len(internal_user_id) < 2
        or internal_user_id[0] != "u"
    ):
        return None
    try:
        return int(internal_user_id[1:])
    except ValueError:
        return None


class PostgresUserIdentityRepository:
    """Telegram id → internal user id; find-or-create is concurrency-safe at the row level."""

    _UPSERT = """
        WITH ins AS (
            INSERT INTO user_identities (telegram_user_id, internal_user_id)
            VALUES ($1::bigint, $2::text)
            ON CONFLICT (telegram_user_id) DO NOTHING
            RETURNING telegram_user_id, internal_user_id
        )
        SELECT telegram_user_id, internal_user_id FROM ins
        UNION ALL
        SELECT u.telegram_user_id, u.internal_user_id
        FROM user_identities u
        WHERE u.telegram_user_id = $1::bigint
        LIMIT 1
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def find_by_telegram_user_id(self, telegram_user_id: int) -> IdentityRecord | None:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT internal_user_id, telegram_user_id
                    FROM user_identities
                    WHERE telegram_user_id = $1::bigint
                    """,
                    telegram_user_id,
                )
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc
        if row is None:
            return None
        return IdentityRecord(
            internal_user_id=row["internal_user_id"],
            telegram_user_id=int(row["telegram_user_id"]),
        )

    async def create_if_absent(self, telegram_user_id: int) -> IdentityRecord:
        internal = internal_user_id_for_telegram(telegram_user_id)
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(self._UPSERT, telegram_user_id, internal)
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc
        if row is None:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_INVARIANT)
        return IdentityRecord(
            internal_user_id=row["internal_user_id"],
            telegram_user_id=int(row["telegram_user_id"]),
        )
