"""Opt-in integration test: retention timestamps migration (005) on real PostgreSQL."""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

from app.persistence.postgres_migrations_main import run_slice1_postgres_migrations_from_env


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL retention timestamps migration integration test")
    return url


def test_postgres_retention_timestamps_migration_schema_effects(
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    monkeypatch.setenv("DATABASE_URL", pg_url)

    async def main() -> None:
        await run_slice1_postgres_migrations_from_env()

        conn = await asyncpg.connect(pg_url)
        try:
            for table_name in ("idempotency_records", "slice1_audit_events"):
                row = await conn.fetchrow(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = $1
                      AND column_name = 'created_at'
                    """,
                    table_name,
                )
                assert row is not None, f"missing created_at on {table_name}"

            for index_name in (
                "idx_slice1_audit_events_created_at",
                "idx_idempotency_records_created_at_completed_true",
            ):
                regclass = await conn.fetchval(
                    "SELECT to_regclass($1::text)",
                    f"public.{index_name}",
                )
                assert regclass is not None, f"missing index {index_name}"
        finally:
            await conn.close()

    asyncio.run(main())
