"""Opt-in UC-05 apply integration tests (DATABASE_URL) — one transaction, durable idempotency."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.domain.billing_apply_rules import UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED
from app.domain.uc05_apply_decision import first_time_decision
from app.persistence.billing_events_ledger_contracts import (
    BillingEventAmountCurrency,
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
)
from app.persistence.billing_subscription_apply_contracts import BillingSubscriptionApplyOutcome
from app.persistence.postgres_billing_events_ledger import PostgresBillingEventsLedgerRepository
from app.persistence.postgres_billing_subscription_apply import PostgresAtomicUC05SubscriptionApply
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.security.errors import InternalErrorCategory, PersistenceDependencyError
from app.shared.types import OperationOutcomeCategory, SubscriptionSnapshotState

BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = BACKEND_ROOT / "migrations"
_PREFIX = "uc05pg_"


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping UC-05 postgres integration tests")
    return url


def _ref(*parts: str) -> str:
    return _PREFIX + "".join(parts)


def _ledger_row(
    *,
    fact_ref: str,
    ext: str = "e1",
    status: BillingEventLedgerStatus = BillingEventLedgerStatus.ACCEPTED,
    user: str | None = f"{_PREFIX}user1",
    event_type: str = UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED,
) -> BillingEventLedgerRecord:
    t = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    return BillingEventLedgerRecord(
        internal_fact_ref=fact_ref,
        billing_provider_key="prov_uc05",
        external_event_id=ext,
        event_type=event_type,
        event_effective_at=t,
        event_received_at=t,
        internal_user_id=user,
        checkout_attempt_id=None,
        amount_currency=BillingEventAmountCurrency(amount_minor_units=1, currency_code="USD"),
        status=status,
        ingestion_correlation_id="corr-uc05",
    )


def test_first_time_decision_does_not_reference_postgres() -> None:
    """Domain decision remains pure: used by tests without DATABASE_URL."""
    ins = first_time_decision(
        _ledger_row(fact_ref="local-only", ext="e99"),
    )
    assert ins.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED


def test_postgres_apply_active_updates_snapshot_idempotency_audit(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            fact = _ref("f1")
            u = f"{_PREFIX}user_apply"
            rec = _ledger_row(fact_ref=fact, user=u, ext="e_apply_1")
            ar = PostgresAtomicUC05SubscriptionApply(pool)
            snap = PostgresSubscriptionSnapshotReader(pool)
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM billing_events_ledger WHERE internal_fact_ref = $1", fact)
                await conn.execute("DELETE FROM billing_subscription_apply_records WHERE internal_fact_ref = $1", fact)
                await conn.execute(
                    "DELETE FROM billing_subscription_apply_audit_events WHERE internal_fact_ref = $1", fact
                )
                await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id = $1", u)

            le = PostgresBillingEventsLedgerRepository(pool)
            await le.append_or_get_by_provider_and_external_id(rec)

            r1 = await ar.apply_by_internal_fact_ref(fact)
            assert r1.operation_outcome is OperationOutcomeCategory.SUCCESS
            assert r1.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED
            assert r1.idempotent_replay is False

            s = await snap.get_for_user(u)
            assert s is not None
            assert s.state_label == SubscriptionSnapshotState.ACTIVE.value

            r2 = await ar.apply_by_internal_fact_ref(fact)
            assert r2.operation_outcome is OperationOutcomeCategory.IDEMPOTENT_NOOP
            assert r2.idempotent_replay is True
            assert r2.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED

            async with pool.acquire() as conn:
                n_apply = await conn.fetchval(
                    "SELECT count(*)::int FROM billing_subscription_apply_records WHERE internal_fact_ref = $1",
                    fact,
                )
                n_aud = await conn.fetchval(
                    "SELECT count(*)::int FROM billing_subscription_apply_audit_events WHERE internal_fact_ref = $1",
                    fact,
                )
            assert n_apply == 1
            assert n_aud == 1
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_no_activation_when_not_accepted(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            fact = _ref("f_dup")
            u = f"{_PREFIX}u_dup"
            rec = _ledger_row(fact_ref=fact, user=u, ext="e_dup_1", status=BillingEventLedgerStatus.DUPLICATE)
            ar = PostgresAtomicUC05SubscriptionApply(pool)
            async with pool.acquire() as conn:
                for tbl, clause in (
                    ("billing_events_ledger", "internal_fact_ref = $1"),
                    ("billing_subscription_apply_records", "internal_fact_ref = $1"),
                    ("billing_subscription_apply_audit_events", "internal_fact_ref = $1"),
                ):
                    await conn.execute(f"DELETE FROM {tbl} WHERE {clause}", fact)

            le = PostgresBillingEventsLedgerRepository(pool)
            await le.append_or_get_by_provider_and_external_id(rec)

            r1 = await ar.apply_by_internal_fact_ref(fact)
            assert r1.apply_outcome is BillingSubscriptionApplyOutcome.NO_ACTIVATION
            snap = PostgresSubscriptionSnapshotReader(pool)
            assert await snap.get_for_user(u) is None
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_apply_invalid_ref_validation(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            ar = PostgresAtomicUC05SubscriptionApply(pool)
            r0 = await ar.apply_by_internal_fact_ref("")
            assert r0.operation_outcome is OperationOutcomeCategory.VALIDATION_FAILURE
            r1 = await ar.apply_by_internal_fact_ref("no spaces or bad!char")
            assert r1.operation_outcome is OperationOutcomeCategory.VALIDATION_FAILURE
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_audit_failure_rolls_back_all_writes(pg_url: str) -> None:
    """If apply audit cannot be appended, the transaction must not leave a partial apply."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            fact = _ref("f_rb")
            u = f"{_PREFIX}u_rb"
            rec = _ledger_row(fact_ref=fact, user=u, ext="e_rb_1")
            le = PostgresBillingEventsLedgerRepository(pool)
            await le.append_or_get_by_provider_and_external_id(rec)
            ar = PostgresAtomicUC05SubscriptionApply(pool)

            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id = $1", u)
            with (
                patch(
                    "app.persistence.postgres_billing_subscription_apply.PostgresBillingSubscriptionApplyAuditAppender"
                    ".append_in_connection",
                    new=AsyncMock(side_effect=PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT)),
                ),
                pytest.raises(PersistenceDependencyError),
            ):
                await ar.apply_by_internal_fact_ref(fact)

            snap = PostgresSubscriptionSnapshotReader(pool)
            assert await snap.get_for_user(u) is None
            async with pool.acquire() as conn:
                n = await conn.fetchval(
                    "SELECT count(*)::int FROM billing_subscription_apply_records WHERE internal_fact_ref = $1",
                    fact,
                )
            assert n == 0
        finally:
            await pool.close()

    asyncio.run(main())
