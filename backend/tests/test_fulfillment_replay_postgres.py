"""Повтор и пересечение платёжных событий проверяются на настоящем PostgreSQL."""

import asyncio
import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.runtime.fulfillment_processor import FulfillmentInput, process_fulfillment
from app.shared.types import OperationOutcomeCategory


@pytest.fixture
async def payment_account():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("Нужен локальный PostgreSQL")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
    uid = f"fulfillment-replay-{uuid.uuid4()}"
    now = datetime.now(UTC)
    deadline = now + timedelta(days=10)
    await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
    await pool.execute(
        "INSERT INTO user_identities (internal_user_id,telegram_user_id) VALUES ($1,$2)",
        uid,
        -uuid.uuid4().int % -(2**60),
    )
    await pool.execute(
        "INSERT INTO subscription_snapshots (internal_user_id,state_label,plan_id,device_count,active_until_utc)"
        " VALUES ($1,'active','1m',5,$2)",
        uid,
        deadline,
    )
    inp = FulfillmentInput(
        provider_key="audit-test",
        external_event_id=str(uuid.uuid4()),
        external_payment_id=str(uuid.uuid4()),
        telegram_user_id=0,
        internal_user_id=uid,
        paid_at=now,
        period_days=30,
        device_count=9,
        amount_kopecks=24900,
    )
    try:
        yield pool, inp, deadline
    finally:
        await pool.execute(
            "DELETE FROM billing_ingestion_audit_events WHERE internal_fact_ref IN "
            "(SELECT internal_fact_ref FROM billing_events_ledger WHERE internal_user_id=$1)",
            uid,
        )
        for table in (
            "billing_subscription_apply_audit_events",
            "billing_subscription_apply_records",
            "billing_events_ledger",
            "referral_transactions",
            "referral_balances",
            "subscription_snapshots",
            "user_identities",
        ):
            await pool.execute(f"DELETE FROM {table} WHERE internal_user_id=$1", uid)
        await pool.close()


async def snapshot(pool, uid):
    return dict(
        await pool.fetchrow(
            "SELECT active_until_utc,plan_id,device_count FROM subscription_snapshots WHERE internal_user_id=$1",
            uid,
        )
    )


async def test_same_event_extends_only_once(payment_account):
    pool, inp, deadline = payment_account
    first = await process_fulfillment(pool=pool, inp=inp)
    assert first.operation_outcome is OperationOutcomeCategory.SUCCESS
    expected = await snapshot(pool, inp.internal_user_id)
    assert expected["active_until_utc"] == deadline + timedelta(days=30)
    second = await process_fulfillment(pool=pool, inp=inp)
    assert second.idempotent_replay
    assert await snapshot(pool, inp.internal_user_id) == expected


async def test_old_event_does_not_overwrite_newer_subscription(payment_account):
    pool, inp, deadline = payment_account
    await process_fulfillment(pool=pool, inp=inp)
    await pool.execute(
        "UPDATE subscription_snapshots SET active_until_utc=$2,plan_id='3m',device_count=12 WHERE internal_user_id=$1",
        inp.internal_user_id,
        deadline + timedelta(days=120),
    )
    expected = await snapshot(pool, inp.internal_user_id)
    assert (await process_fulfillment(pool=pool, inp=inp)).idempotent_replay
    assert await snapshot(pool, inp.internal_user_id) == expected


async def test_two_payments_preserve_both_periods(payment_account, monkeypatch):
    pool, inp, deadline = payment_account
    original = PostgresSubscriptionSnapshotReader.get_for_user_in_connection

    async def slow_read(conn, uid):
        result = await original(conn, uid)
        # До исправления обе транзакции успевали прочитать старый срок.
        await asyncio.sleep(0.1)
        return result

    monkeypatch.setattr(PostgresSubscriptionSnapshotReader, "get_for_user_in_connection", slow_read)
    second = replace(inp, external_event_id=str(uuid.uuid4()), external_payment_id=str(uuid.uuid4()))
    results = await asyncio.gather(process_fulfillment(pool=pool, inp=inp), process_fulfillment(pool=pool, inp=second))
    assert all(result.operation_outcome is OperationOutcomeCategory.SUCCESS for result in results)
    assert (await snapshot(pool, inp.internal_user_id))["active_until_utc"] == deadline + timedelta(days=60)


async def test_enrichment_failure_rolls_back_apply_and_can_retry(payment_account, monkeypatch):
    pool, inp, deadline = payment_account
    original = PostgresSubscriptionSnapshotReader.upsert_state_in_connection

    async def fail_enrichment(conn, state):
        if state.active_until_utc is not None:
            raise RuntimeError("тестовый отказ обогащения")
        return await original(conn, state)

    monkeypatch.setattr(PostgresSubscriptionSnapshotReader, "upsert_state_in_connection", fail_enrichment)
    with pytest.raises(RuntimeError, match="отказ обогащения"):
        await process_fulfillment(pool=pool, inp=inp)
    assert (await snapshot(pool, inp.internal_user_id))["active_until_utc"] == deadline
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM billing_subscription_apply_records WHERE internal_user_id=$1",
            inp.internal_user_id,
        )
        == 0
    )
    monkeypatch.setattr(PostgresSubscriptionSnapshotReader, "upsert_state_in_connection", original)
    assert (await process_fulfillment(pool=pool, inp=inp)).operation_outcome is OperationOutcomeCategory.SUCCESS
    actual = await snapshot(pool, inp.internal_user_id)
    assert actual["active_until_utc"] == deadline + timedelta(days=30)
    assert actual["device_count"] == 9
