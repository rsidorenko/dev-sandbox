"""Платёжная админка не смешивает списание, применение и выдачу доступа."""

import os
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.app import build_web_api_app


@pytest.mark.asyncio
async def test_payment_stages_and_filters(monkeypatch):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("Нужен изолированный PostgreSQL")
    for key, value in {
        "WEB_ADMIN_ENABLE": "1",
        "WEB_ADMIN_USERNAME": "admin",
        "WEB_ADMIN_PASSWORD": "test-password",
        "JWT_SECRET": "test-secret",
        "WEB_API_DEV_INSECURE_COOKIE": "1",
    }.items():
        monkeypatch.setenv(key, value)
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
    uid = "admin-payment-fixture"

    async def cleanup():
        await pool.execute("DELETE FROM billing_subscription_apply_records WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM billing_events_ledger WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM referral_transactions WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", uid)

    await cleanup()
    try:
        await pool.execute("INSERT INTO user_identities(internal_user_id,telegram_user_id) VALUES($1,9900800000)", uid)
        for kind, event in [
            ("subscription_activated", "missing-apply"),
            ("device_topup", "devices"),
            ("subscription_activated", "applied"),
        ]:
            await pool.execute(
                "INSERT INTO billing_events_ledger(internal_fact_ref,billing_provider_key,external_event_id,event_type,event_effective_at,event_received_at,internal_user_id,amount_minor_units,currency_code,status,ingestion_correlation_id) "
                "VALUES($1,'yookassa',$1,$2,NOW()-INTERVAL '1 hour',NOW()-INTERVAL '1 hour',$3,24900,'RUB','accepted','test')",
                uid + event,
                kind,
                uid,
            )
        await pool.execute(
            "INSERT INTO billing_subscription_apply_records(internal_fact_ref,internal_user_id,apply_outcome) VALUES($1,$2,'active_applied')",
            uid + "applied",
            uid,
        )
        await pool.execute(
            "INSERT INTO referral_transactions(transaction_id,internal_user_id,amount_kopecks,transaction_type,idempotency_key,created_at) VALUES($1,$2,-24900,'subscription_payment','secret-operation-canary',NOW()-INTERVAL '1 hour')",
            uid + "balance",
            uid,
        )
        await pool.execute(
            "INSERT INTO balance_access_jobs(internal_user_id,operation_key,status,created_at) VALUES($1,'secret-operation-canary','pending',NOW()-INTERVAL '1 hour')",
            uid,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/admin/payments")).status_code == 401
            assert (
                await client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "test-password"})
            ).status_code == 200
            response = await client.get("/api/v1/admin/payments", params={"search": uid})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["total"] == 4
            assert "secret-operation-canary" not in response.text
            rows = {r["reference"]: r for r in data["payments"]}
            assert rows[uid + "devices"]["attention_reason"] is None
            assert rows[uid + "devices"]["apply_status"] == "not_applicable"
            assert rows[uid + "applied"]["access_status"] == "unknown"
            assert rows[uid + "missing-apply"]["attention_reason"] == "subscription_not_applied"
            assert rows[uid + "balance"]["attention_reason"] == "access_delayed"
            assert rows[uid + "balance"]["amount_minor_units"] == 24900
            attention = await client.get("/api/v1/admin/payments", params={"search": uid, "attention": "1"})
            assert attention.json()["total"] == 2
            balance = await client.get("/api/v1/admin/payments", params={"search": "9900800000", "provider": "balance"})
            assert balance.json()["total"] == 1
            await pool.execute(
                "UPDATE balance_access_jobs SET operation_key='different-operation' WHERE internal_user_id=$1", uid
            )
            unrelated = (
                await client.get("/api/v1/admin/payments", params={"search": uid, "provider": "balance"})
            ).json()
            assert unrelated["payments"][0]["access_status"] == "legacy_unknown"
            assert unrelated["payments"][0]["attention_reason"] is None
            assert (await client.get("/api/v1/admin/payments", params={"search": uid, "page": "2"})).json()[
                "payments"
            ] == []
    finally:
        await cleanup()
        await pool.close()
