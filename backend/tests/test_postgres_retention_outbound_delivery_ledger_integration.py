"""Opt-in integration: retention for UC-01 outbound delivery ledger (sent-only) on PostgreSQL."""

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

_TEST_KEY_SENT_OLD = "itest-s1odl-sent-old"
_TEST_KEY_SENT_NEW = "itest-s1odl-sent-new"
_TEST_KEY_PEND_OLD = "itest-s1odl-pend-old"

_NOW_UTC = datetime(2010, 1, 2, 12, 0, 0, tzinfo=UTC)
_OLD_CREATED_AT = datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC)
_FRESH_CREATED_AT = datetime(2010, 1, 2, 11, 30, 0, tzinfo=UTC)

_LEDGER_KEYS = (_TEST_KEY_SENT_OLD, _TEST_KEY_SENT_NEW, _TEST_KEY_PEND_OLD)


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL outbound delivery retention integration test")
    return url


async def _insert_ledger_rows(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        INSERT INTO slice1_uc01_outbound_deliveries (
            idempotency_key, delivery_status, telegram_message_id,
            last_attempt_at, created_at, updated_at
        )
        VALUES ($1::text, 'sent', 1001::bigint, NULL::timestamptz, $2::timestamptz, $2::timestamptz)
        """,
        _TEST_KEY_SENT_OLD,
        _OLD_CREATED_AT,
    )
    await conn.execute(
        """
        INSERT INTO slice1_uc01_outbound_deliveries (
            idempotency_key, delivery_status, telegram_message_id,
            last_attempt_at, created_at, updated_at
        )
        VALUES ($1::text, 'sent', 1002::bigint, NULL::timestamptz, $2::timestamptz, $2::timestamptz)
        """,
        _TEST_KEY_SENT_NEW,
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
        _TEST_KEY_PEND_OLD,
        _OLD_CREATED_AT,
    )


async def _cleanup_ledger(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "DELETE FROM slice1_uc01_outbound_deliveries WHERE idempotency_key = ANY($1::text[])",
        list(_LEDGER_KEYS),
    )


def test_postgres_retention_outbound_ledger_dry_run_counts_old_sent_only(
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    monkeypatch.setenv("DATABASE_URL", pg_url)

    async def main() -> None:
        await run_slice1_postgres_migrations_from_env()
        conn = await asyncpg.connect(pg_url)
        try:
            await _insert_ledger_rows(conn)
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
                assert result.outbound_delivery_rows_matched == 1
                assert result.outbound_delivery_rows_deleted == 0
                for key in _LEDGER_KEYS:
                    row = await conn.fetchrow(
                        "SELECT 1 FROM slice1_uc01_outbound_deliveries WHERE idempotency_key = $1::text",
                        key,
                    )
                    assert row is not None, f"missing ledger row {key}"
            finally:
                await _cleanup_ledger(conn)
        finally:
            await conn.close()

    asyncio.run(main())


def test_postgres_retention_outbound_ledger_delete_sent_only_preserves_pending(
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    monkeypatch.setenv("DATABASE_URL", pg_url)

    async def main() -> None:
        await run_slice1_postgres_migrations_from_env()
        conn = await asyncpg.connect(pg_url)
        try:
            await _insert_ledger_rows(conn)
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
                assert result.outbound_delivery_rows_deleted == 1
                assert result.outbound_delivery_rows_matched == 0

                assert (
                    await conn.fetchrow(
                        "SELECT 1 FROM slice1_uc01_outbound_deliveries WHERE idempotency_key = $1::text",
                        _TEST_KEY_SENT_OLD,
                    )
                    is None
                )
                assert (
                    await conn.fetchrow(
                        "SELECT 1 FROM slice1_uc01_outbound_deliveries WHERE idempotency_key = $1::text",
                        _TEST_KEY_SENT_NEW,
                    )
                    is not None
                )
                row_p = await conn.fetchrow(
                    """
                    SELECT delivery_status FROM slice1_uc01_outbound_deliveries
                    WHERE idempotency_key = $1::text
                    """,
                    _TEST_KEY_PEND_OLD,
                )
                assert row_p is not None
                assert str(row_p["delivery_status"]) == "pending"
            finally:
                await _cleanup_ledger(conn)
        finally:
            await conn.close()

    asyncio.run(main())
