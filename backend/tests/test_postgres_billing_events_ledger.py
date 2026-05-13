"""Opt-in contract tests for PostgresBillingEventsLedgerRepository (DATABASE_URL), mirroring in-memory tests."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from app.admin_support import (
    Adm02BillingFactsCategory,
    Adm02BillingFactsLedgerReadAdapter,
)
from app.persistence import (
    BillingEventAmountCurrency,
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
    BillingEventsLedgerUserSummary,
    BillingFactsPresenceCategory,
    PostgresBillingEventsLedgerRepository,
)
from app.persistence.postgres_migrations import apply_postgres_migrations

BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = BACKEND_ROOT / "migrations"
_PREFIX = "test_pbel_"


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL billing events ledger tests")
    return url


def _make_record(
    *,
    internal_fact_ref: str,
    external_event_id: str = "ext-evt-1",
    event_received_at: datetime | None = None,
    event_effective_at: datetime | None = None,
    internal_user_id: str | None = "user-1",
    status: BillingEventLedgerStatus = BillingEventLedgerStatus.ACCEPTED,
) -> BillingEventLedgerRecord:
    t_eff = event_effective_at or datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    t_rec = event_received_at or datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    return BillingEventLedgerRecord(
        internal_fact_ref=internal_fact_ref,
        billing_provider_key="provider_a",
        external_event_id=external_event_id,
        event_type="payment_succeeded",
        event_effective_at=t_eff,
        event_received_at=t_rec,
        internal_user_id=internal_user_id,
        checkout_attempt_id=None,
        amount_currency=BillingEventAmountCurrency(
            amount_minor_units=1000,
            currency_code="USD",
        ),
        status=status,
        ingestion_correlation_id="corr-1",
    )


async def _cleanup_and_migrate(pool: asyncpg.Pool) -> None:
    await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM billing_events_ledger WHERE internal_fact_ref LIKE $1::text",
            f"{_PREFIX}%",
        )


def test_postgres_table_has_no_jsonb_and_expected_columns(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT column_name, udt_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'billing_events_ledger'
                    ORDER BY ordinal_position
                    """
                )
            udt = {str(r["column_name"]): str(r["udt_name"]) for r in rows}
            assert "jsonb" not in udt.values()
            assert "json" not in udt.values()
            for col in (
                "internal_fact_ref",
                "billing_provider_key",
                "external_event_id",
                "ingestion_correlation_id",
            ):
                assert col in udt, udt
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_append_new_record_and_read_round_trip(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            ref = f"{_PREFIX}be-1"
            record = _make_record(internal_fact_ref=ref, external_event_id="evt-rt-1")
            repo = PostgresBillingEventsLedgerRepository(pool)
            stored = await repo.append_or_get_by_provider_and_external_id(record)
            assert stored == record
            async with pool.acquire() as conn:
                n = await conn.fetchval(
                    "SELECT count(*)::bigint FROM billing_events_ledger WHERE internal_fact_ref = $1", ref
                )
            assert n == 1
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_append_same_provider_and_external_id_is_idempotent(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            repo = PostgresBillingEventsLedgerRepository(pool)
            first = _make_record(
                internal_fact_ref=f"{_PREFIX}be-1",
                external_event_id="evt-idem-1",
            )
            second = _make_record(
                internal_fact_ref=f"{_PREFIX}be-2",
                external_event_id="evt-idem-1",
            )
            stored_first = await repo.append_or_get_by_provider_and_external_id(first)
            stored_second = await repo.append_or_get_by_provider_and_external_id(second)
            assert stored_first == stored_second
            assert stored_first.internal_fact_ref == f"{_PREFIX}be-1"
            async with pool.acquire() as conn:
                n = await conn.fetchval(
                    "SELECT count(*)::bigint FROM billing_events_ledger WHERE billing_provider_key = $1 AND external_event_id = $2",
                    "provider_a",
                    "evt-idem-1",
                )
            assert n == 1
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_summary_for_user_without_accepted_is_none_category(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            repo = PostgresBillingEventsLedgerRepository(pool)
            summary = await repo.get_user_billing_facts_summary("missing-user-xyz")
            assert isinstance(summary, BillingEventsLedgerUserSummary)
            assert summary.category is BillingFactsPresenceCategory.NONE
            assert summary.internal_fact_refs == ()
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_summary_user_with_accepted_records_has_accepted(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            repo = PostgresBillingEventsLedgerRepository(pool)
            user_id = f"{_PREFIX}user-1"
            other = f"{_PREFIX}user-2"
            t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
            t1 = datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC)
            t2 = datetime(2026, 1, 1, 12, 0, 2, tzinfo=UTC)
            await repo.append_or_get_by_provider_and_external_id(
                _make_record(
                    internal_fact_ref=f"{_PREFIX}be-1",
                    internal_user_id=user_id,
                    external_event_id="evt-1",
                    event_received_at=t0,
                )
            )
            await repo.append_or_get_by_provider_and_external_id(
                _make_record(
                    internal_fact_ref=f"{_PREFIX}be-2",
                    internal_user_id=user_id,
                    external_event_id="evt-2",
                    event_received_at=t1,
                )
            )
            await repo.append_or_get_by_provider_and_external_id(
                _make_record(
                    internal_fact_ref=f"{_PREFIX}be-3",
                    internal_user_id=other,
                    external_event_id="evt-3",
                    event_received_at=t2,
                )
            )
            summary = await repo.get_user_billing_facts_summary(user_id)
            assert summary.category is BillingFactsPresenceCategory.HAS_ACCEPTED
            assert summary.internal_fact_refs == (f"{_PREFIX}be-1", f"{_PREFIX}be-2")
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_summary_ignores_non_accepted_status(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            repo = PostgresBillingEventsLedgerRepository(pool)
            user_id = f"{_PREFIX}u-ig"
            await repo.append_or_get_by_provider_and_external_id(
                _make_record(
                    internal_fact_ref=f"{_PREFIX}ig-1",
                    internal_user_id=user_id,
                    external_event_id="evt-ig-1",
                    status=BillingEventLedgerStatus.IGNORED,
                )
            )
            await repo.append_or_get_by_provider_and_external_id(
                _make_record(
                    internal_fact_ref=f"{_PREFIX}acc-1",
                    internal_user_id=user_id,
                    external_event_id="evt-ac-1",
                    status=BillingEventLedgerStatus.ACCEPTED,
                )
            )
            summary = await repo.get_user_billing_facts_summary(user_id)
            assert summary.category is BillingFactsPresenceCategory.HAS_ACCEPTED
            assert summary.internal_fact_refs == (f"{_PREFIX}acc-1",)
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_append_preserves_order_in_summary_deterministic(pg_url: str) -> None:
    """ORDER BY event_received_at ASC, internal_fact_ref ASC matches in-memory for monotonic times."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            repo = PostgresBillingEventsLedgerRepository(pool)
            t0 = datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC)
            t1 = datetime(2026, 2, 1, 10, 0, 1, tzinfo=UTC)
            base = f"{_PREFIX}ord_"
            await repo.append_or_get_by_provider_and_external_id(
                _make_record(
                    internal_fact_ref=f"{base}be-1",
                    internal_user_id="ord-user",
                    external_event_id="evt-ord-1",
                    event_received_at=t0,
                )
            )
            await repo.append_or_get_by_provider_and_external_id(
                _make_record(
                    internal_fact_ref=f"{base}be-2",
                    internal_user_id="ord-user",
                    external_event_id="evt-ord-2",
                    event_received_at=t1,
                )
            )
            s = await repo.get_user_billing_facts_summary("ord-user")
            assert s.internal_fact_refs == (f"{base}be-1", f"{base}be-2")
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_amount_currency_none_round_trip(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            ref = f"{_PREFIX}nullcur"
            rec = _make_record(internal_fact_ref=ref, external_event_id="evt-null-amt")
            no_amt = BillingEventLedgerRecord(
                internal_fact_ref=rec.internal_fact_ref,
                billing_provider_key=rec.billing_provider_key,
                external_event_id=rec.external_event_id,
                event_type=rec.event_type,
                event_effective_at=rec.event_effective_at,
                event_received_at=rec.event_received_at,
                internal_user_id=rec.internal_user_id,
                checkout_attempt_id=rec.checkout_attempt_id,
                amount_currency=None,
                status=rec.status,
                ingestion_correlation_id=rec.ingestion_correlation_id,
            )
            repo = PostgresBillingEventsLedgerRepository(pool)
            got = await repo.append_or_get_by_provider_and_external_id(no_amt)
            assert got.amount_currency is None
        finally:
            await pool.close()

    asyncio.run(main())


def test_adm02_adapter_with_postgres_ledger_and_two_users(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            repo = PostgresBillingEventsLedgerRepository(pool)
            adapter = Adm02BillingFactsLedgerReadAdapter(repo)
            u_ok = f"{_PREFIX}adm02_ok"
            u_empty = f"{_PREFIX}adm02_empty"
            await repo.append_or_get_by_provider_and_external_id(
                _make_record(
                    internal_fact_ref=f"{_PREFIX}adm_f1",
                    internal_user_id=u_ok,
                    external_event_id="adm-evt-1",
                )
            )
            d_ok = await adapter.get_billing_facts_diagnostics(u_ok)
            d_empty = await adapter.get_billing_facts_diagnostics(u_empty)
            assert d_ok.category is Adm02BillingFactsCategory.HAS_ACCEPTED
            assert d_empty.category is Adm02BillingFactsCategory.NONE
        finally:
            await pool.close()

    asyncio.run(main())


def test_postgres_get_by_internal_fact_ref(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await _cleanup_and_migrate(pool)
            ref = f"{_PREFIX}gref1"
            rec = _make_record(internal_fact_ref=ref, external_event_id="gext-1")
            repo = PostgresBillingEventsLedgerRepository(pool)
            await repo.append_or_get_by_provider_and_external_id(rec)
            got = await repo.get_by_internal_fact_ref(ref)
            assert got is not None
            assert got.internal_fact_ref == ref
            assert await repo.get_by_internal_fact_ref("no-such-ref") is None
        finally:
            await pool.close()

    asyncio.run(main())
