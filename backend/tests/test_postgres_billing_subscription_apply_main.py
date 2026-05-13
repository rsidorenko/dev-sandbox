"""Opt-in: Postgres + UC-05 operator async_main (DATABASE_URL)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from app.application.billing_subscription_apply_main import BILLING_SUBSCRIPTION_APPLY_ENABLE, async_main
from app.domain.billing_apply_rules import UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED
from app.domain.uc05_apply_decision import first_time_decision
from app.persistence.billing_events_ledger_contracts import (
    BillingEventAmountCurrency,
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
)
from app.persistence.postgres_billing_events_ledger import PostgresBillingEventsLedgerRepository
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.shared.types import SubscriptionSnapshotState

BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = BACKEND_ROOT / "migrations"
_PREFIX = "t_pbapply_"


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping billing subscription apply main integration tests")
    return url


def _ref(s: str) -> str:
    return _PREFIX + s


def _row(
    *,
    fact_ref: str,
    ext: str = "e1",
    user: str | None = None,
) -> BillingEventLedgerRecord:
    u = user or _ref("u1")
    t = datetime(2026, 4, 10, 8, 0, 0, tzinfo=UTC)
    return BillingEventLedgerRecord(
        internal_fact_ref=fact_ref,
        billing_provider_key="pbapply_prov",
        external_event_id=ext,
        event_type=UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED,
        event_effective_at=t,
        event_received_at=t,
        internal_user_id=u,
        checkout_attempt_id=None,
        amount_currency=BillingEventAmountCurrency(amount_minor_units=1, currency_code="USD"),
        status=BillingEventLedgerStatus.ACCEPTED,
        ingestion_correlation_id=_ref("c"),
    )


def test_ledger_row_used_by_main_matches_domain_allowlist() -> None:
    """Ingest+apply path reuses the same first_time_decision as UC-05 (no DB)."""
    ins = first_time_decision(_row(fact_ref="local", ext="x"))
    assert ins.apply_outcome is not None


def test_postgres_apply_main_ledger_then_apply_idempotent(
    pg_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def body() -> None:
        fact = _ref("fact1")
        user = _ref("user1")
        ext = _ref("ext_evt")
        monkeypatch.setenv(BILLING_SUBSCRIPTION_APPLY_ENABLE, "1")
        monkeypatch.setenv("BOT_TOKEN", "x" * 20)
        monkeypatch.setenv("DATABASE_URL", pg_url)
        monkeypatch.setenv("APP_ENV", "test")
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            le = PostgresBillingEventsLedgerRepository(pool)
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM billing_events_ledger WHERE internal_fact_ref = $1", fact)
                await conn.execute("DELETE FROM billing_subscription_apply_records WHERE internal_fact_ref = $1", fact)
                await conn.execute(
                    "DELETE FROM billing_subscription_apply_audit_events WHERE internal_fact_ref = $1", fact
                )
                await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id = $1", user)
            rec = _row(fact_ref=fact, ext=ext, user=user)
            await le.append_or_get_by_provider_and_external_id(rec)
            c1 = await async_main(["--internal-fact-ref", fact])
            cap1 = capsys.readouterr()
            out1, err1 = cap1.out, cap1.err
        finally:
            await pool.close()
        assert c1 == 0
        assert err1 == ""
        line1 = out1.strip()
        assert line1.count("\n") == 0
        assert "billing_subscription_apply: ok" in line1
        assert f"internal_fact_ref={fact}" in line1
        assert "outcome=success" in line1
        assert "state=active_applied" in line1
        # second run: idempotent
        c2 = await async_main(["--internal-fact-ref", fact])
        cap2 = capsys.readouterr()
        out2, err2 = cap2.out, cap2.err
        assert c2 == 0
        assert err2 == ""
        assert "outcome=idempotent_noop" in out2
        assert "state=active_applied" in out2

        pool2 = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            snap = PostgresSubscriptionSnapshotReader(pool2)
            s = await snap.get_for_user(user)
            assert s is not None
            assert s.state_label == SubscriptionSnapshotState.ACTIVE.value
            async with pool2.acquire() as conn:
                n_apply = await conn.fetchval(
                    "SELECT count(*)::int FROM billing_subscription_apply_records WHERE internal_fact_ref = $1",
                    fact,
                )
                n_aud = await conn.fetchval(
                    "SELECT count(*)::int FROM billing_subscription_apply_audit_events WHERE internal_fact_ref = $1",
                    fact,
                )
        finally:
            await pool2.close()
        assert n_apply == 1
        assert n_aud == 1

    asyncio.run(body())
