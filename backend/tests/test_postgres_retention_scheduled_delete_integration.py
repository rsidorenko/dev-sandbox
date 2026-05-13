"""Opt-in integration test: scheduled slice-1 retention wrapper destructive path."""

from __future__ import annotations

import asyncio
import os
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO

import asyncpg
import pytest

from app.persistence.postgres_migrations_main import run_slice1_postgres_migrations_from_env
from app.persistence.slice1_retention_scheduled_main import run_slice1_retention_scheduled_from_env

# Narrow test identifiers (point deletes in finally).
_TEST_AUDIT_CORR_OLD = "itest-s1sched-del-audit-old"
_TEST_AUDIT_CORR_NEW = "itest-s1sched-del-audit-new"
_TEST_IDEMP_KEY_OLD_DONE = "itest-s1sched-del-idem-old-done"
_TEST_IDEMP_KEY_NEW_DONE = "itest-s1sched-del-idem-new-done"
_TEST_IDEMP_KEY_OLD_NOT_DONE = "itest-s1sched-del-idem-old-notdone"


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL scheduled retention delete integration test")
    return url


def test_postgres_retention_scheduled_delete_path_requires_explicit_opt_in(
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("SLICE1_RETENTION_TTL_SECONDS", "3600")
    monkeypatch.setenv("SLICE1_RETENTION_BATCH_LIMIT", "10")
    monkeypatch.setenv("SLICE1_RETENTION_MAX_ROUNDS", "5")
    monkeypatch.setenv("SLICE1_RETENTION_DRY_RUN", "0")
    monkeypatch.setenv("SLICE1_RETENTION_SCHEDULED_ENABLE_DELETE", "1")

    async def main() -> None:
        await run_slice1_postgres_migrations_from_env()

        conn = await asyncpg.connect(pg_url)
        try:
            now = datetime.now(UTC)
            old = now - timedelta(hours=2)
            fresh = now - timedelta(minutes=30)
            await conn.execute(
                """
                INSERT INTO slice1_audit_events
                    (correlation_id, operation, outcome, internal_category, created_at)
                VALUES ($1::text, $2::text, $3::text, NULL::text, $4::timestamptz)
                """,
                _TEST_AUDIT_CORR_OLD,
                "itest_op",
                "itest_outcome",
                old,
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
                fresh,
            )
            await conn.execute(
                """
                INSERT INTO idempotency_records (idempotency_key, completed, created_at)
                VALUES ($1::text, true::boolean, $2::timestamptz)
                """,
                _TEST_IDEMP_KEY_OLD_DONE,
                old,
            )
            await conn.execute(
                """
                INSERT INTO idempotency_records (idempotency_key, completed, created_at)
                VALUES ($1::text, true::boolean, $2::timestamptz)
                """,
                _TEST_IDEMP_KEY_NEW_DONE,
                fresh,
            )
            await conn.execute(
                """
                INSERT INTO idempotency_records (idempotency_key, completed, created_at)
                VALUES ($1::text, false::boolean, $2::timestamptz)
                """,
                _TEST_IDEMP_KEY_OLD_NOT_DONE,
                old,
            )
            try:
                buf = StringIO()
                with redirect_stdout(buf):
                    await run_slice1_retention_scheduled_from_env()
                out = buf.getvalue()
                assert "slice1_retention_scheduled_cleanup" in out
                assert "dry_run=False" in out
                assert "outbound_delivery_rows_matched=" in out
                assert "outbound_delivery_rows_deleted=" in out

                old_audit = await conn.fetchrow(
                    "SELECT 1 FROM slice1_audit_events WHERE correlation_id = $1::text",
                    _TEST_AUDIT_CORR_OLD,
                )
                new_audit = await conn.fetchrow(
                    "SELECT 1 FROM slice1_audit_events WHERE correlation_id = $1::text",
                    _TEST_AUDIT_CORR_NEW,
                )
                old_done = await conn.fetchrow(
                    "SELECT 1 FROM idempotency_records WHERE idempotency_key = $1::text",
                    _TEST_IDEMP_KEY_OLD_DONE,
                )
                new_done = await conn.fetchrow(
                    "SELECT 1 FROM idempotency_records WHERE idempotency_key = $1::text",
                    _TEST_IDEMP_KEY_NEW_DONE,
                )
                old_not_done = await conn.fetchrow(
                    "SELECT 1 FROM idempotency_records WHERE idempotency_key = $1::text",
                    _TEST_IDEMP_KEY_OLD_NOT_DONE,
                )

                assert old_audit is None, "old eligible audit row was not deleted"
                assert old_done is None, "old eligible completed idempotency row was not deleted"
                assert new_audit is not None, "fresh audit row should survive"
                assert new_done is not None, "fresh completed idempotency row should survive"
                assert old_not_done is not None, "old not-completed idempotency row should survive"
            finally:
                await conn.execute(
                    "DELETE FROM slice1_audit_events WHERE correlation_id = ANY($1::text[])",
                    [_TEST_AUDIT_CORR_OLD, _TEST_AUDIT_CORR_NEW],
                )
                await conn.execute(
                    "DELETE FROM idempotency_records WHERE idempotency_key = ANY($1::text[])",
                    [
                        _TEST_IDEMP_KEY_OLD_DONE,
                        _TEST_IDEMP_KEY_NEW_DONE,
                        _TEST_IDEMP_KEY_OLD_NOT_DONE,
                    ],
                )
        finally:
            await conn.close()

    asyncio.run(main())
