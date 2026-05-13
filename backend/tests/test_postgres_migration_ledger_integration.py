"""Opt-in integration test: slice-1 migration ledger on real PostgreSQL."""

from __future__ import annotations

import asyncio
import os
import re

import asyncpg
import pytest

from app.persistence.postgres_migrations_main import run_slice1_postgres_migrations_from_env

_EXPECTED_LEDGER_FILENAMES = (
    "001_user_identities.sql",
    "002_idempotency_records.sql",
    "003_subscription_snapshots.sql",
    "004_slice1_audit_events.sql",
    "005_retention_timestamps.sql",
    "006_slice1_uc01_outbound_deliveries.sql",
    "007_slice1_uc01_outbound_deliveries_sent_retention_index.sql",
    "008_billing_events_ledger.sql",
    "009_billing_ingestion_audit_events.sql",
    "010_billing_subscription_apply.sql",
    "011_issuance_state.sql",
    "012_adm02_ensure_access_audit_events.sql",
    "013_telegram_update_dedup.sql",
    "014_subscription_lifecycle_v1.sql",
    "015_access_reconcile_runs.sql",
)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL migration ledger integration test")
    return url


def _assert_ledger_checksum_rows(rows: list[asyncpg.Record]) -> dict[str, str]:
    expected_n = len(_EXPECTED_LEDGER_FILENAMES)
    assert len(rows) == expected_n, f"expected {expected_n} ledger rows for known filenames, got {len(rows)}"
    mapping: dict[str, str] = {}
    for row in rows:
        filename = str(row["filename"])
        assert filename not in mapping, f"duplicate ledger row for {filename!r}"
        raw_checksum = row["checksum"]
        assert raw_checksum is not None, filename
        checksum = str(raw_checksum)
        assert _SHA256_HEX_RE.match(checksum) is not None, filename
        mapping[filename] = checksum
    assert set(mapping) == set(_EXPECTED_LEDGER_FILENAMES)
    return mapping


def test_postgres_migration_ledger_two_runs_idempotent(
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    monkeypatch.setenv("DATABASE_URL", pg_url)

    async def main() -> None:
        await run_slice1_postgres_migrations_from_env()

        conn = await asyncpg.connect(pg_url)
        try:
            regclass = await conn.fetchval(
                "SELECT to_regclass($1::text)",
                "public.schema_migration_ledger",
            )
            assert regclass is not None

            rows_first = await conn.fetch(
                """
                SELECT filename, checksum
                FROM schema_migration_ledger
                WHERE filename = ANY($1::text[])
                ORDER BY filename
                """,
                list(_EXPECTED_LEDGER_FILENAMES),
            )
            first_map = _assert_ledger_checksum_rows(list(rows_first))
        finally:
            await conn.close()

        await run_slice1_postgres_migrations_from_env()

        conn = await asyncpg.connect(pg_url)
        try:
            regclass = await conn.fetchval(
                "SELECT to_regclass($1::text)",
                "public.schema_migration_ledger",
            )
            assert regclass is not None

            rows_second = await conn.fetch(
                """
                SELECT filename, checksum
                FROM schema_migration_ledger
                WHERE filename = ANY($1::text[])
                ORDER BY filename
                """,
                list(_EXPECTED_LEDGER_FILENAMES),
            )
            second_map = _assert_ledger_checksum_rows(list(rows_second))
        finally:
            await conn.close()

        assert first_map == second_map

    asyncio.run(main())
