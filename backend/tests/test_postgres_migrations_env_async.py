"""Opt-in async integration test for env-based PostgreSQL migration entrypoint."""

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
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL env migration integration test")
    return url


def test_postgres_migrations_env_entrypoint_creates_slice1_tables(
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    monkeypatch.setenv("DATABASE_URL", pg_url)

    async def main() -> None:
        await run_slice1_postgres_migrations_from_env()

        conn = await asyncpg.connect(pg_url)
        try:
            for table_name in (
                "user_identities",
                "idempotency_records",
                "subscription_snapshots",
                "slice1_audit_events",
            ):
                regclass = await conn.fetchval(
                    "SELECT to_regclass($1::text)",
                    f"public.{table_name}",
                )
                assert regclass is not None
        finally:
            await conn.close()

    asyncio.run(main())
