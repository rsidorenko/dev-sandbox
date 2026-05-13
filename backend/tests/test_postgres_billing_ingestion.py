"""Opt-in: Postgres + IngestNormalizedBillingFactHandler / PostgresAtomicBillingIngestion (DATABASE_URL)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import asyncpg
import pytest

from app.application.billing_ingestion import IngestNormalizedBillingFactHandler, NormalizedBillingFactInput
from app.persistence import (
    BILLING_INGESTION_OUTCOME_ACCEPTED,
    BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY,
    BillingEventAmountCurrency,
    BillingEventLedgerStatus,
    PostgresBillingEventsLedgerRepository,
    PostgresBillingIngestionAuditAppender,
)
from app.persistence.postgres_billing_ingestion_atomic import PostgresAtomicBillingIngestion
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.security.errors import PersistenceDependencyError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = BACKEND_ROOT / "migrations"
_PREFIX = "test_pbing_"


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL billing ingestion tests")
    return url


async def _cleanup_and_migrate(pool: asyncpg.Pool) -> None:
    await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM billing_ingestion_audit_events WHERE external_event_id LIKE $1::text",
            f"{_PREFIX}%",
        )
        await conn.execute(
            "DELETE FROM billing_events_ledger WHERE external_event_id LIKE $1::text",
            f"{_PREFIX}%",
        )


def test_postgres_ingest_handler_persists_and_idempotent_replay(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            repo = PostgresBillingEventsLedgerRepository(pool)
            audit = PostgresBillingIngestionAuditAppender(pool)
            handler = IngestNormalizedBillingFactHandler(repo, audit)
            t0 = datetime(2026, 1, 10, 8, 0, 0, tzinfo=UTC)
            t1 = datetime(2026, 1, 10, 8, 0, 1, tzinfo=UTC)
            ext = f"{_PREFIX}ext-1"
            in1 = NormalizedBillingFactInput(
                billing_provider_key="provider_ingest_ci",
                external_event_id=ext,
                event_type="payment_succeeded",
                event_effective_at=t0,
                event_received_at=t1,
                status=BillingEventLedgerStatus.ACCEPTED,
                ingestion_correlation_id="corr-pg-1",
                internal_user_id=f"{_PREFIX}u1",
                amount_currency=BillingEventAmountCurrency(
                    amount_minor_units=100,
                    currency_code="USD",
                ),
            )
            r1 = await handler.handle(in1)
            assert r1.is_idempotent_replay is False
            ref1 = r1.record.internal_fact_ref
            r2 = await handler.handle(
                NormalizedBillingFactInput(
                    billing_provider_key="provider_ingest_ci",
                    external_event_id=ext,
                    event_type="payment_succeeded",
                    event_effective_at=t0,
                    event_received_at=t1,
                    status=BillingEventLedgerStatus.ACCEPTED,
                    ingestion_correlation_id="corr-pg-2",
                    internal_user_id=f"{_PREFIX}u1",
                )
            )
            assert r2.is_idempotent_replay is True
            assert r2.record.internal_fact_ref == ref1
            async with pool.acquire() as conn:
                n = await conn.fetchval(
                    "SELECT count(*)::bigint FROM billing_events_ledger WHERE external_event_id = $1", ext
                )
            assert n == 1
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT outcome, is_idempotent_replay, internal_fact_ref, billing_event_status
                    FROM billing_ingestion_audit_events
                    WHERE external_event_id = $1::text
                    ORDER BY occurred_at ASC, audit_event_id ASC
                    """,
                    ext,
                )
            assert len(rows) == 2
            assert str(rows[0]["outcome"]) == BILLING_INGESTION_OUTCOME_ACCEPTED
            assert str(rows[1]["outcome"]) == BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY
            assert rows[0]["is_idempotent_replay"] is False
            assert rows[1]["is_idempotent_replay"] is True
            assert str(rows[0]["internal_fact_ref"]) == str(rows[1]["internal_fact_ref"]) == ref1
            assert str(rows[0]["billing_event_status"]) == "accepted"
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_atomic_ingest_new_fact_and_idempotent_replay(pg_url: str) -> None:
    """Single-txn path: new fact + replay each commit ledger+audit together."""
    ext = f"{_PREFIX}atomic-replay"

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            atomic = PostgresAtomicBillingIngestion(pool)
            t0 = datetime(2026, 1, 12, 8, 0, 0, tzinfo=UTC)
            t1 = datetime(2026, 1, 12, 8, 0, 1, tzinfo=UTC)
            in1 = NormalizedBillingFactInput(
                billing_provider_key="provider_atomic_ci",
                external_event_id=ext,
                event_type="payment_succeeded",
                event_effective_at=t0,
                event_received_at=t1,
                status=BillingEventLedgerStatus.ACCEPTED,
                ingestion_correlation_id="corr-atomic-1",
            )
            r1 = await atomic.ingest_normalized_billing_fact(in1)
            assert r1.is_idempotent_replay is False
            ref1 = r1.record.internal_fact_ref
            r2 = await atomic.ingest_normalized_billing_fact(
                NormalizedBillingFactInput(
                    billing_provider_key="provider_atomic_ci",
                    external_event_id=ext,
                    event_type="payment_succeeded",
                    event_effective_at=t0,
                    event_received_at=t1,
                    status=BillingEventLedgerStatus.ACCEPTED,
                    ingestion_correlation_id="corr-atomic-2",
                )
            )
            assert r2.is_idempotent_replay is True
            assert r2.record.internal_fact_ref == ref1
            async with pool.acquire() as conn:
                n_ledger = await conn.fetchval(
                    "SELECT count(*)::bigint FROM billing_events_ledger WHERE external_event_id = $1", ext
                )
                n_audit = await conn.fetchval(
                    "SELECT count(*)::bigint FROM billing_ingestion_audit_events WHERE external_event_id = $1", ext
                )
            assert n_ledger == 1
            assert n_audit == 2
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_atomic_audit_check_failure_rolls_back_new_ledger_row(pg_url: str) -> None:
    """Invalid audit outcome fails the txn: no new ledger row, no audit row."""
    ext = f"{_PREFIX}atomic-bad-audit"

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            atomic = PostgresAtomicBillingIngestion(pool)
            t0 = datetime(2026, 1, 13, 8, 0, 0, tzinfo=UTC)
            in1 = NormalizedBillingFactInput(
                billing_provider_key="provider_atomic_ci",
                external_event_id=ext,
                event_type="payment_succeeded",
                event_effective_at=t0,
                event_received_at=t0,
                status=BillingEventLedgerStatus.ACCEPTED,
                ingestion_correlation_id="corr-bad-1",
            )
            with (
                patch(
                    "app.persistence.postgres_billing_ingestion_atomic.BILLING_INGESTION_OUTCOME_ACCEPTED",
                    "___invalid_outcome_for_check___",
                ),
                pytest.raises(PersistenceDependencyError),
            ):
                await atomic.ingest_normalized_billing_fact(in1)
            async with pool.acquire() as conn:
                n_ledger = await conn.fetchval(
                    "SELECT count(*)::bigint FROM billing_events_ledger WHERE external_event_id = $1", ext
                )
                n_audit = await conn.fetchval(
                    "SELECT count(*)::bigint FROM billing_ingestion_audit_events WHERE external_event_id = $1", ext
                )
            assert n_ledger == 0
            assert n_audit == 0
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_atomic_replay_audit_failure_leaves_prior_commits_unchanged(pg_url: str) -> None:
    """Replay: prior row committed; audit insert failure rolls back only this attempt (no 2nd audit)."""
    ext = f"{_PREFIX}atomic-replay-fail"

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            atomic = PostgresAtomicBillingIngestion(pool)
            t0 = datetime(2026, 1, 14, 8, 0, 0, tzinfo=UTC)
            in1 = NormalizedBillingFactInput(
                billing_provider_key="provider_atomic_ci",
                external_event_id=ext,
                event_type="payment_succeeded",
                event_effective_at=t0,
                event_received_at=t0,
                status=BillingEventLedgerStatus.ACCEPTED,
                ingestion_correlation_id="corr-r1",
            )
            r1 = await atomic.ingest_normalized_billing_fact(in1)
            assert r1.is_idempotent_replay is False
            in2 = NormalizedBillingFactInput(
                billing_provider_key="provider_atomic_ci",
                external_event_id=ext,
                event_type="payment_succeeded",
                event_effective_at=t0,
                event_received_at=t0,
                status=BillingEventLedgerStatus.ACCEPTED,
                ingestion_correlation_id="corr-r2",
            )
            with (
                patch(
                    "app.persistence.postgres_billing_ingestion_atomic.BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY",
                    "___invalid_replay_outcome___",
                ),
                pytest.raises(PersistenceDependencyError),
            ):
                await atomic.ingest_normalized_billing_fact(in2)
            async with pool.acquire() as conn:
                n_ledger = await conn.fetchval(
                    "SELECT count(*)::bigint FROM billing_events_ledger WHERE external_event_id = $1", ext
                )
                n_audit = await conn.fetchval(
                    "SELECT count(*)::bigint FROM billing_ingestion_audit_events WHERE external_event_id = $1", ext
                )
            assert n_ledger == 1
            assert n_audit == 1
        finally:
            await pool.close()

    asyncio.run(main())
