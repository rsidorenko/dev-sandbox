"""Focused tests for Postgres Telegram update dedup guard."""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

from app.application.telegram_update_dedup import dedup_key_hash_for_update
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_telegram_update_dedup import PostgresTelegramUpdateDedupGuard
from app.security.errors import PersistenceDependencyError


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL Telegram dedup integration tests")
    return url


def test_postgres_dedup_first_seen_then_duplicate(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        guard = PostgresTelegramUpdateDedupGuard(pool, ttl_seconds=600.0)
        key_hash = dedup_key_hash_for_update(command_bucket="status", telegram_update_id=444001)
        try:
            await apply_postgres_migrations(pool)
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM telegram_update_dedup WHERE dedup_key_hash = $1::text", key_hash)
            assert await guard.mark_if_first_seen(command_bucket="status", telegram_update_id=444001) is True
            assert await guard.mark_if_first_seen(command_bucket="status", telegram_update_id=444001) is False
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM telegram_update_dedup WHERE dedup_key_hash = $1::text", key_hash)
            await pool.close()

    asyncio.run(main())


def test_postgres_dedup_same_update_different_bucket_independent(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        guard = PostgresTelegramUpdateDedupGuard(pool, ttl_seconds=600.0)
        key_status = dedup_key_hash_for_update(command_bucket="status", telegram_update_id=444002)
        key_access = dedup_key_hash_for_update(command_bucket="access_resend", telegram_update_id=444002)
        try:
            await apply_postgres_migrations(pool)
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM telegram_update_dedup WHERE dedup_key_hash = ANY($1::text[])",
                    [key_status, key_access],
                )
            assert await guard.mark_if_first_seen(command_bucket="status", telegram_update_id=444002) is True
            assert await guard.mark_if_first_seen(command_bucket="access_resend", telegram_update_id=444002) is True
        finally:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM telegram_update_dedup WHERE dedup_key_hash = ANY($1::text[])",
                    [key_status, key_access],
                )
            await pool.close()

    asyncio.run(main())


def test_postgres_dedup_expired_row_allows_first_seen_again(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        guard = PostgresTelegramUpdateDedupGuard(pool, ttl_seconds=600.0)
        key_hash = dedup_key_hash_for_update(command_bucket="status", telegram_update_id=444003)
        try:
            await apply_postgres_migrations(pool)
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM telegram_update_dedup WHERE dedup_key_hash = $1::text", key_hash)
                await conn.execute(
                    """
                    INSERT INTO telegram_update_dedup (
                        dedup_key_hash,
                        command_bucket,
                        first_seen_at,
                        expires_at,
                        source_marker
                    )
                    VALUES ($1::text, 'status', now() - interval '20 minute', now() - interval '10 minute', 'test')
                    ON CONFLICT (dedup_key_hash) DO UPDATE SET expires_at = excluded.expires_at
                    """,
                    key_hash,
                )
            assert await guard.mark_if_first_seen(command_bucket="status", telegram_update_id=444003) is True
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM telegram_update_dedup WHERE dedup_key_hash = $1::text", key_hash)
            await pool.close()

    asyncio.run(main())


def test_postgres_dedup_hash_not_raw_update_id() -> None:
    hashed = dedup_key_hash_for_update(command_bucket="status", telegram_update_id=555666777)
    assert "555666777" not in hashed
    assert "status" not in hashed
    assert len(hashed) == 64


def test_postgres_dedup_storage_failure_raises_dependency_error() -> None:
    class _BrokenPool:
        def acquire(self):
            raise OSError("transient transport issue")

    async def main() -> None:
        guard = PostgresTelegramUpdateDedupGuard(_BrokenPool())  # type: ignore[arg-type]
        with pytest.raises(PersistenceDependencyError):
            await guard.mark_if_first_seen(command_bucket="status", telegram_update_id=1)

    asyncio.run(main())
