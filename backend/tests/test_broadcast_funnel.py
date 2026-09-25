"""Явная атрибуция бонусных кнопок и подтверждённая ledger-выручка."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import asyncpg
import pytest

from app.application.broadcast_funnel import (
    checkout_dedup_key,
    parse_payment_callback,
    payment_callback,
    record_bonus_event,
)
from app.application.funnel_events import record_interaction
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.admin_broadcast_funnel import BROADCAST_FUNNEL_SQL

JOB = "eeeeeeee-1111-2222-3333-444444444444"


def test_callback_roundtrip_and_byte_limit():
    for plan in ("1d", "7d", "14d", "1m", "3m", "6m", "365d"):
        for devices in (5, 20):
            cb = payment_callback(JOB, plan, devices)
            assert len(cb.encode()) <= 64
            assert parse_payment_callback(cb) == (JOB, plan, devices)


@pytest.mark.parametrize(
    "value", ["bp:no:1m:5", f"bp:{JOB}:1m:4", f"bp:{JOB}:1m:21", f"bp:{JOB}:bad:5", "do_pay:1m:5", "bp:"]
)
def test_invalid_callback_rejected(value):
    assert parse_payment_callback(value) is None


async def test_analytics_failure_does_not_break_checkout():
    pool = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("offline")))
    await record_bonus_event(pool, job_id=JOB, internal_user_id="test", stage="bonus_claimed")
    pool.execute.assert_awaited_once()


async def test_stalled_analytics_is_cancelled():
    cancelled = asyncio.Event()

    async def stalled(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    pool = SimpleNamespace(execute=stalled)
    async with asyncio.timeout(1):
        await record_bonus_event(pool, job_id=JOB, internal_user_id="test", stage="bonus_claimed")
    assert cancelled.is_set()


async def test_report_only_counts_matching_accepted_payment_and_deduplicates(monkeypatch):
    monkeypatch.setenv("FUNNEL_EVENTS_ENABLE", "1")
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("Нужна тестовая PostgreSQL")
    pool = await asyncpg.create_pool(url)
    uid = "bonus-funnel-test"
    try:
        await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
        await pool.execute(
            "INSERT INTO user_identities(internal_user_id,telegram_user_id,created_at) VALUES($1,998888889991,now())",
            uid,
        )
        await pool.execute(
            "INSERT INTO broadcast_jobs(job_id,created_by,status,message_text,bonus) VALUES($1,'test','completed','test','{\"type\":\"promo\",\"code\":\"TEST\"}')",
            JOB,
        )
        for _ in range(2):
            for stage in ("bonus_claimed", "plan_selected", "payment_created"):
                await record_bonus_event(
                    pool,
                    job_id=JOB,
                    internal_user_id=uid,
                    stage=stage,
                    payment_id="bonus-test-payment" if stage == "payment_created" else None,
                )
        await pool.execute(
            "INSERT INTO user_identities(internal_user_id,telegram_user_id,created_at) VALUES($1,998888889992,now())",
            uid + "-2",
        )
        for actor in (uid, uid + "-2"):
            assert await record_interaction(
                pool,
                event_name="checkout_opened",
                surface="telegram",
                internal_user_id=actor,
                dedup_key=checkout_dedup_key(JOB, actor, "1m", 5),
                plan_id="1m",
                device_count=5,
            )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM funnel_interactions WHERE internal_user_id IN ($1,$2)", uid, uid + "-2"
            )
            == 2
        )
        rows = await pool.fetch(BROADCAST_FUNNEL_SQL)
        row = next(r for r in rows if r["job_id"] == UUID(JOB))
        assert (row["claimed_users"], row["selected_users"], row["payment_users"], row["payments_created"]) == (
            1,
            1,
            1,
            1,
        )
        assert row["paid_users"] == 0
        sql = "INSERT INTO billing_events_ledger (internal_fact_ref,billing_provider_key,external_event_id,event_type,event_effective_at,event_received_at,internal_user_id,amount_minor_units,currency_code,status,ingestion_correlation_id) VALUES($1,'yookassa_v1',$2,'subscription_activated',now(),now(),$3,9900,'RUB','accepted','bonus-test')"
        # Обычный платёж того же пользователя не относится к бонусному checkout.
        await pool.execute(sql, "bonus-test-unrelated", "yookassa:ordinary-payment", uid)
        row = next(r for r in await pool.fetch(BROADCAST_FUNNEL_SQL) if r["job_id"] == UUID(JOB))
        assert row["paid_users"] == 0
        await pool.execute(sql, "bonus-test-accepted", "yookassa:bonus-test-payment", uid)
        row = next(r for r in await pool.fetch(BROADCAST_FUNNEL_SQL) if r["job_id"] == UUID(JOB))
        assert (row["paid_users"], row["payments_succeeded"], row["revenue_kopecks"]) == (1, 1, 9900)
    finally:
        await pool.execute("DELETE FROM billing_events_ledger WHERE ingestion_correlation_id='bonus-test'")
        await pool.execute("DELETE FROM broadcast_jobs WHERE job_id=$1", JOB)
        await pool.execute("DELETE FROM funnel_interactions WHERE internal_user_id IN ($1,$2)", uid, uid + "-2")
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id IN ($1,$2)", uid, uid + "-2")
        await pool.close()
