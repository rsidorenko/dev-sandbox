"""Обзор использует реальные состояния доступа, валюту и московские сутки."""

import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.app import build_web_api_app


@pytest.mark.asyncio
async def test_overview_money_entitlements_and_auth(monkeypatch):
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

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 23, 0, 30, tzinfo=UTC)

    monkeypatch.setattr("app.web_api.admin_overview.datetime", FixedDateTime)
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
    ids = ["overview-fixture-" + str(i) for i in range(4)]

    async def cleanup():
        await pool.execute("DELETE FROM billing_events_ledger WHERE internal_user_id=ANY($1)", ids)
        await pool.execute("DELETE FROM subscription_snapshots WHERE internal_user_id=ANY($1)", ids)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=ANY($1)", ids)

    await cleanup()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/admin/overview")).status_code == 401
            assert (
                await client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "test-password"})
            ).status_code == 200
            before = (await client.get("/api/v1/admin/overview")).json()
            await pool.executemany(
                "INSERT INTO user_identities(internal_user_id,telegram_user_id,is_internal,created_at,subscription_token) VALUES($1,$2,$3,'2026-09-22 22:00Z','secret-overview-canary-'||$1)",
                [(uid, 9900700000 + i, i == 1) for i, uid in enumerate(ids)],
            )
            await pool.executemany(
                "INSERT INTO subscription_snapshots(internal_user_id,state_label,trial_expires_at,keys_deleted_at,updated_at) VALUES($1,'active',$2,$3,NOW())",
                [
                    (ids[0], datetime(2026, 9, 24, tzinfo=UTC), None),
                    (ids[1], datetime(2026, 9, 24, tzinfo=UTC), None),
                    (ids[2], datetime(2026, 9, 24, tzinfo=UTC), datetime(2026, 9, 22, tzinfo=UTC)),
                    (ids[3], datetime(2026, 9, 22, tzinfo=UTC), None),
                ],
            )
            for n, (uid, currency, amount, status, provider, when) in enumerate(
                [
                    (ids[0], "RUB", 24900, "accepted", "yookassa", "2026-09-22 22:00Z"),
                    (ids[0], "RUB", None, "accepted", "yookassa", "2026-09-22 22:00Z"),
                    (ids[0], "USD", 99900, "accepted", "yookassa", "2026-09-22 22:00Z"),
                    (ids[1], "RUB", 99900, "accepted", "yookassa", "2026-09-22 22:00Z"),
                    (ids[0], "RUB", 99900, "accepted", "manual_grant", "2026-09-22 22:00Z"),
                    (ids[0], "RUB", 99900, "accepted", "yookassa", "2026-09-22 20:59Z"),
                ]
            ):
                await pool.execute(
                    "INSERT INTO billing_events_ledger(internal_fact_ref,billing_provider_key,external_event_id,event_type,event_effective_at,event_received_at,internal_user_id,amount_minor_units,currency_code,status,ingestion_correlation_id) VALUES($1,$2,$1,'subscription_activated',$3,NOW(),$4,$5,$6,$7,'test')",
                    f"overview-fixture-{n}",
                    provider,
                    datetime.fromisoformat(when),
                    uid,
                    amount,
                    currency,
                    status,
                )
            await pool.execute(
                "INSERT INTO billing_events_ledger(internal_fact_ref,billing_provider_key,external_event_id,event_type,event_effective_at,event_received_at,internal_user_id,amount_minor_units,currency_code,status,ingestion_correlation_id) VALUES('overview-fixture-other','yookassa','overview-fixture-other','other_fact','2026-09-22 22:00Z',NOW(),$1,99900,'RUB','accepted','test')",
                ids[0],
            )
            result = await client.get("/api/v1/admin/overview")
            assert result.status_code == 200, result.text
            data = result.json()
            assert data["day"] == "2026-09-23"
            assert data["active_users"] - before["active_users"] == 2
            assert data["new_users"] - before["new_users"] == 3
            assert data["revenue_kopecks"] - before["revenue_kopecks"] == 24900
            assert data["payments"] - before["payments"] == 2
            assert data["unknown_amount_payments"] - before["unknown_amount_payments"] == 1
            assert sum(data["observations"].values()) == data["connections"]
            assert "secret-overview-canary" not in result.text
    finally:
        await cleanup()
        await pool.close()
