"""Contract tests for PostgresSubscriptionSnapshotReader against PostgreSQL (opt-in via DATABASE_URL)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from app.application.interfaces import SubscriptionSnapshot
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader

BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = BACKEND_ROOT / "migrations"
_INTERNAL_PREFIX = "test_ss_pg_"


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL subscription snapshot reader tests")
    return url


def test_postgres_subscription_snapshot_returns_none_when_missing(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            internal_user_id = f"{_INTERNAL_PREFIX}missing_1"
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM subscription_snapshots WHERE internal_user_id = $1::text",
                    internal_user_id,
                )
            reader = PostgresSubscriptionSnapshotReader(pool)
            assert await reader.get_for_user(internal_user_id) is None
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_subscription_snapshot_returns_row_contract_shape(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            internal_user_id = f"{_INTERNAL_PREFIX}present_1"
            state_label = "inactive"
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM subscription_snapshots WHERE internal_user_id = $1::text",
                    internal_user_id,
                )
                await conn.execute(
                    """
                    INSERT INTO subscription_snapshots (internal_user_id, state_label)
                    VALUES ($1::text, $2::text)
                    """,
                    internal_user_id,
                    state_label,
                )
            reader = PostgresSubscriptionSnapshotReader(pool)
            got = await reader.get_for_user(internal_user_id)
            assert got == SubscriptionSnapshot(internal_user_id=internal_user_id, state_label=state_label)
            assert got.active_until_utc is None
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_put_if_absent_inserts_and_skips_existing_state(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            internal_user_id = f"{_INTERNAL_PREFIX}put_absent_1"
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM subscription_snapshots WHERE internal_user_id = $1::text",
                    internal_user_id,
                )
            adapter = PostgresSubscriptionSnapshotReader(pool)
            await adapter.put_if_absent(
                SubscriptionSnapshot(internal_user_id=internal_user_id, state_label="inactive"),
            )
            assert await adapter.get_for_user(internal_user_id) == SubscriptionSnapshot(
                internal_user_id=internal_user_id,
                state_label="inactive",
            )
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE subscription_snapshots
                    SET state_label = $2::text
                    WHERE internal_user_id = $1::text
                    """,
                    internal_user_id,
                    "needs_review",
                )
            await adapter.put_if_absent(
                SubscriptionSnapshot(internal_user_id=internal_user_id, state_label="inactive"),
            )
            got = await adapter.get_for_user(internal_user_id)
            assert got == SubscriptionSnapshot(internal_user_id=internal_user_id, state_label="needs_review")
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_upsert_state_updates_or_inserts(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            internal_user_id = f"{_INTERNAL_PREFIX}upsert_1"
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM subscription_snapshots WHERE internal_user_id = $1::text",
                    internal_user_id,
                )
            adapter = PostgresSubscriptionSnapshotReader(pool)
            await adapter.upsert_state(SubscriptionSnapshot(internal_user_id=internal_user_id, state_label="inactive"))
            assert await adapter.get_for_user(internal_user_id) == SubscriptionSnapshot(
                internal_user_id=internal_user_id,
                state_label="inactive",
            )
            await adapter.upsert_state(SubscriptionSnapshot(internal_user_id=internal_user_id, state_label="active"))
            assert await adapter.get_for_user(internal_user_id) == SubscriptionSnapshot(
                internal_user_id=internal_user_id,
                state_label="active",
            )
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_upsert_state_persists_active_until_timestamp(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            internal_user_id = f"{_INTERNAL_PREFIX}active_until_1"
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM subscription_snapshots WHERE internal_user_id = $1::text",
                    internal_user_id,
                )
            active_until = datetime(2030, 1, 1, 0, 0, 0, tzinfo=UTC)
            adapter = PostgresSubscriptionSnapshotReader(pool)
            await adapter.upsert_state(
                SubscriptionSnapshot(
                    internal_user_id=internal_user_id,
                    state_label="active",
                    active_until_utc=active_until,
                )
            )
            got = await adapter.get_for_user(internal_user_id)
            assert got == SubscriptionSnapshot(
                internal_user_id=internal_user_id,
                state_label="active",
                active_until_utc=active_until,
            )
        finally:
            await pool.close()

    asyncio.run(main())
