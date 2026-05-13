"""Opt-in integration test: slice-1 retention cleanup delete path on real PostgreSQL."""

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
_TEST_AUDIT_CORR_OLD = "itest-s1del-audit-old"
_TEST_AUDIT_CORR_NEW = "itest-s1del-audit-new"
_TEST_IDEMP_KEY_OLD_TRUE = "itest-s1del-idem-old-true"
_TEST_IDEMP_KEY_NEW_TRUE = "itest-s1del-idem-new-true"
_TEST_IDEMP_KEY_OLD_FALSE = "itest-s1del-idem-old-false"
_TEST_LEDGER_SENT_OLD = "itest-s1del-ledger-sent-old"
_TEST_LEDGER_SENT_NEW = "itest-s1del-ledger-sent-new"
_TEST_LEDGER_PEND_OLD = "itest-s1del-ledger-pend-old"

# Fixed clock: cutoff in 2010 so rows with created_at ~ now() are outside delete window.
_NOW_UTC = datetime(2010, 1, 2, 12, 0, 0, tzinfo=UTC)
_OLD_CREATED_AT = datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC)
_FRESH_CREATED_AT = datetime(2010, 1, 2, 11, 30, 0, tzinfo=UTC)

_TEST_AUDIT_CORR_ALL = (
    _TEST_AUDIT_CORR_OLD,
    _TEST_AUDIT_CORR_NEW,
)
_TEST_IDEMP_KEYS_ALL = (
    _TEST_IDEMP_KEY_OLD_TRUE,
    _TEST_IDEMP_KEY_NEW_TRUE,
    _TEST_IDEMP_KEY_OLD_FALSE,
)
_TEST_LEDGER_KEYS_ALL = (
    _TEST_LEDGER_SENT_OLD,
    _TEST_LEDGER_SENT_NEW,
    _TEST_LEDGER_PEND_OLD,
)


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL retention cleanup delete integration test")
    return url


def test_postgres_retention_cleanup_delete_removes_old_only(
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
                _TEST_IDEMP_KEY_OLD_TRUE,
                _OLD_CREATED_AT,
            )
            await conn.execute(
                """
                INSERT INTO idempotency_records (idempotency_key, completed, created_at)
                VALUES ($1::text, true::boolean, $2::timestamptz)
                """,
                _TEST_IDEMP_KEY_NEW_TRUE,
                _FRESH_CREATED_AT,
            )
            await conn.execute(
                """
                INSERT INTO idempotency_records (idempotency_key, completed, created_at)
                VALUES ($1::text, false::boolean, $2::timestamptz)
                """,
                _TEST_IDEMP_KEY_OLD_FALSE,
                _OLD_CREATED_AT,
            )
            await conn.execute(
                """
                INSERT INTO slice1_uc01_outbound_deliveries (
                    idempotency_key, delivery_status, telegram_message_id,
                    last_attempt_at, created_at, updated_at
                )
                VALUES ($1::text, 'sent', 8001::bigint, NULL::timestamptz, $2::timestamptz, $2::timestamptz)
                """,
                _TEST_LEDGER_SENT_OLD,
                _OLD_CREATED_AT,
            )
            await conn.execute(
                """
                INSERT INTO slice1_uc01_outbound_deliveries (
                    idempotency_key, delivery_status, telegram_message_id,
                    last_attempt_at, created_at, updated_at
                )
                VALUES ($1::text, 'sent', 8002::bigint, NULL::timestamptz, $2::timestamptz, $2::timestamptz)
                """,
                _TEST_LEDGER_SENT_NEW,
                _FRESH_CREATED_AT,
            )
            await conn.execute(
                """
                INSERT INTO slice1_uc01_outbound_deliveries (
                    idempotency_key, delivery_status, telegram_message_id,
                    last_attempt_at, created_at, updated_at
                )
                VALUES ($1::text, 'pending', NULL::bigint, NULL::timestamptz, $2::timestamptz, $2::timestamptz)
                """,
                _TEST_LEDGER_PEND_OLD,
                _OLD_CREATED_AT,
            )

            try:
                settings = RetentionSettings(
                    ttl_seconds=3600,
                    batch_limit=10,
                    dry_run=False,
                    max_rounds=5,
                )
                result = await run_slice1_retention_cleanup(
                    conn,
                    now_utc=_NOW_UTC,
                    settings=settings,
                )

                assert result.dry_run is False
                assert result.audit_rows == 1
                assert result.idempotency_rows == 1
                assert result.outbound_delivery_rows_deleted == 1
                assert result.outbound_delivery_rows_matched == 0
                assert result.rounds >= 1

                assert (
                    await conn.fetchrow(
                        "SELECT 1 FROM slice1_audit_events WHERE correlation_id = $1::text",
                        _TEST_AUDIT_CORR_OLD,
                    )
                    is None
                )
                assert (
                    await conn.fetchrow(
                        "SELECT 1 FROM idempotency_records WHERE idempotency_key = $1::text",
                        _TEST_IDEMP_KEY_OLD_TRUE,
                    )
                    is None
                )

                assert (
                    await conn.fetchrow(
                        "SELECT 1 FROM slice1_audit_events WHERE correlation_id = $1::text",
                        _TEST_AUDIT_CORR_NEW,
                    )
                    is not None
                )
                assert (
                    await conn.fetchrow(
                        "SELECT 1 FROM idempotency_records WHERE idempotency_key = $1::text",
                        _TEST_IDEMP_KEY_NEW_TRUE,
                    )
                    is not None
                )
                row_false = await conn.fetchrow(
                    """
                    SELECT completed FROM idempotency_records
                    WHERE idempotency_key = $1::text
                    """,
                    _TEST_IDEMP_KEY_OLD_FALSE,
                )
                assert row_false is not None
                assert row_false["completed"] is False

                assert (
                    await conn.fetchrow(
                        "SELECT 1 FROM slice1_uc01_outbound_deliveries WHERE idempotency_key = $1::text",
                        _TEST_LEDGER_SENT_OLD,
                    )
                    is None
                )
                assert (
                    await conn.fetchrow(
                        "SELECT 1 FROM slice1_uc01_outbound_deliveries WHERE idempotency_key = $1::text",
                        _TEST_LEDGER_SENT_NEW,
                    )
                    is not None
                )
                row_lp = await conn.fetchrow(
                    """
                    SELECT delivery_status FROM slice1_uc01_outbound_deliveries
                    WHERE idempotency_key = $1::text
                    """,
                    _TEST_LEDGER_PEND_OLD,
                )
                assert row_lp is not None
                assert str(row_lp["delivery_status"]) == "pending"
            finally:
                await conn.execute(
                    "DELETE FROM slice1_uc01_outbound_deliveries WHERE idempotency_key = ANY($1::text[])",
                    list(_TEST_LEDGER_KEYS_ALL),
                )
                await conn.execute(
                    "DELETE FROM slice1_audit_events WHERE correlation_id = ANY($1::text[])",
                    list(_TEST_AUDIT_CORR_ALL),
                )
                await conn.execute(
                    "DELETE FROM idempotency_records WHERE idempotency_key = ANY($1::text[])",
                    list(_TEST_IDEMP_KEYS_ALL),
                )
        finally:
            await conn.close()

    asyncio.run(main())
