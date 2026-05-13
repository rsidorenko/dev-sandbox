"""Opt-in integration test: slice-1 retention cleanup dry-run counts on real PostgreSQL."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import asyncpg
import pytest

from app.persistence.postgres_migrations_main import run_slice1_postgres_migrations_from_env
from app.persistence.slice1_retention_manual_cleanup import (
    RetentionSettings,
    run_slice1_retention_cleanup,
)

# Narrow test identifiers (point deletes in finally).
_TEST_AUDIT_CORR_OLD = "itest-s1ret-audit-old"
_TEST_AUDIT_CORR_NEW = "itest-s1ret-audit-new"
_TEST_IDEMP_KEY_OLD = "itest-s1ret-idem-old"
_TEST_IDEMP_KEY_NEW = "itest-s1ret-idem-new"
_TEST_LEDGER_SENT_OLD = "itest-s1ret-ledger-sent-old"

# Fixed clock: cutoff lands in 2010 so typical DB rows (created_at ~ now) are not counted.
_NOW_UTC = datetime(2010, 1, 2, 12, 0, 0, tzinfo=UTC)
_OLD_CREATED_AT = datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC)
_FRESH_CREATED_AT = datetime(2010, 1, 2, 11, 30, 0, tzinfo=UTC)


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL retention cleanup dry-run integration test")
    return url


def test_postgres_retention_cleanup_dry_run_counts_and_no_delete(
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    monkeypatch.setenv("DATABASE_URL", pg_url)

    async def main() -> None:
        await run_slice1_postgres_migrations_from_env()

        conn = await asyncpg.connect(pg_url)
        try:
            await conn.execute(
                """
                INSERT INTO slice1_audit_events
                    (correlation_id, operation, outcome, internal_category, created_at)
                VALUES ($1::text, $2::text, $3::text, NULL::text, $4::timestamptz)
                """,
                _TEST_AUDIT_CORR_OLD,
                "itest_op",
                "itest_outcome",
                _OLD_CREATED_AT,
            )
            await conn.execute(
                """
                INSERT INTO slice1_audit_events
                    (correlation_id, operation, outcome, internal_category, created_at)
                VALUES ($1::text, $2::text, $3::text, NULL::text, $4::timestamptz)
                """,
                _TEST_AUDIT_CORR_NEW,
                "itest_op",
                "itest_outcome",
                _FRESH_CREATED_AT,
            )
            await conn.execute(
                """
                INSERT INTO idempotency_records (idempotency_key, completed, created_at)
                VALUES ($1::text, true::boolean, $2::timestamptz)
                """,
                _TEST_IDEMP_KEY_OLD,
                _OLD_CREATED_AT,
            )
            await conn.execute(
                """
                INSERT INTO idempotency_records (idempotency_key, completed, created_at)
                VALUES ($1::text, true::boolean, $2::timestamptz)
                """,
                _TEST_IDEMP_KEY_NEW,
                _FRESH_CREATED_AT,
            )
            await conn.execute(
                """
                INSERT INTO slice1_uc01_outbound_deliveries (
                    idempotency_key, delivery_status, telegram_message_id,
                    last_attempt_at, created_at, updated_at
                )
                VALUES ($1::text, 'sent', 9001::bigint, NULL::timestamptz, $2::timestamptz, $2::timestamptz)
                """,
                _TEST_LEDGER_SENT_OLD,
                _OLD_CREATED_AT,
            )

            try:
                settings = RetentionSettings(
                    ttl_seconds=3600,
                    batch_limit=10,
                    dry_run=True,
                    max_rounds=5,
                )
                result = await run_slice1_retention_cleanup(
                    conn,
                    now_utc=_NOW_UTC,
                    settings=settings,
                )

                assert result.dry_run is True
                assert result.audit_rows == 1
                assert result.idempotency_rows == 1
                assert result.outbound_delivery_rows_matched == 1
                assert result.outbound_delivery_rows_deleted == 0
                assert result.rounds == 0

                for corr in (_TEST_AUDIT_CORR_OLD, _TEST_AUDIT_CORR_NEW):
                    row = await conn.fetchrow(
                        "SELECT 1 FROM slice1_audit_events WHERE correlation_id = $1::text",
                        corr,
                    )
                    assert row is not None, f"missing audit row {corr}"

                for key in (_TEST_IDEMP_KEY_OLD, _TEST_IDEMP_KEY_NEW):
                    row = await conn.fetchrow(
                        "SELECT 1 FROM idempotency_records WHERE idempotency_key = $1::text",
                        key,
                    )
                    assert row is not None, f"missing idempotency row {key}"
                assert (
                    await conn.fetchrow(
                        "SELECT 1 FROM slice1_uc01_outbound_deliveries WHERE idempotency_key = $1::text",
                        _TEST_LEDGER_SENT_OLD,
                    )
                    is not None
                )
            finally:
                await conn.execute(
                    "DELETE FROM slice1_uc01_outbound_deliveries WHERE idempotency_key = $1::text",
                    _TEST_LEDGER_SENT_OLD,
                )
                await conn.execute(
                    "DELETE FROM slice1_audit_events WHERE correlation_id = ANY($1::text[])",
                    [_TEST_AUDIT_CORR_OLD, _TEST_AUDIT_CORR_NEW],
                )
                await conn.execute(
                    "DELETE FROM idempotency_records WHERE idempotency_key = ANY($1::text[])",
                    [_TEST_IDEMP_KEY_OLD, _TEST_IDEMP_KEY_NEW],
                )
        finally:
            await conn.close()

    asyncio.run(main())
