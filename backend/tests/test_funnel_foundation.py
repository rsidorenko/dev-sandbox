"""Проверки аналитики без вмешательства в биллинг и доступ."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest

from app.application.funnel_events import callback_interaction, record_interaction
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.admin_stats import _mature_funnel
from app.web_api.app import build_web_api_app


async def _login_client(pool):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test")
    response = await client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "s3cr3t-pass"})
    assert response.status_code == 200
    return client, {"x-csrf-token": response.json()["csrf_token"]}


async def test_disabled_and_invalid_events_do_not_access_pool(monkeypatch):
    pool = AsyncMock()
    monkeypatch.delenv("FUNNEL_EVENTS_ENABLE", raising=False)
    args = {"event_name": "setup_started", "surface": "telegram", "dedup_key": "1", "telegram_user_id": 1}
    assert not await record_interaction(pool, **args)
    monkeypatch.setenv("FUNNEL_EVENTS_ENABLE", "1")
    assert not await record_interaction(pool, **{**args, "event_name": "vless://secret"})
    assert not await record_interaction(pool, **{**args, "platform": "secret"})
    pool.execute.assert_not_called()


async def test_failure_timeout_and_cancellation(monkeypatch, caplog):
    monkeypatch.setenv("FUNNEL_EVENTS_ENABLE", "1")
    args = {"event_name": "setup_started", "surface": "telegram", "dedup_key": "1", "telegram_user_id": 1}
    pool = AsyncMock()
    pool.execute.side_effect = RuntimeError("SECRET")
    assert not await record_interaction(pool, **args)
    assert "SECRET" not in caplog.text

    async def slow(*args):
        await asyncio.sleep(1)

    pool.execute.side_effect = slow
    assert not await record_interaction(pool, **args)
    pool.execute.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await record_interaction(pool, **args)


@pytest.mark.parametrize("platform", ["ios", "android", "mac", "win", "tv"])
def test_callback_mapping(platform):
    assert callback_interaction(f"connect_{platform}") == ("platform_selected", platform)
    assert callback_interaction(f"connect_fast_{platform}") == ("platform_selected", platform)
    assert callback_interaction(f"{platform}_yes") == ("connection_confirmed", platform)
    assert callback_interaction(f"{platform}_no") == ("connection_problem", platform)
    assert callback_interaction("connect_device") == ("setup_started", "")
    assert callback_interaction("confirm_pay:1m:1") == ("checkout_opened", "")
    assert callback_interaction("main_menu") is None


@pytest.fixture
async def pool(monkeypatch):
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("Нужна отдельная тестовая PostgreSQL DATABASE_URL")
    monkeypatch.setenv("FUNNEL_EVENTS_ENABLE", "1")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    await apply_postgres_migrations(pool)
    await apply_postgres_migrations(pool)
    try:
        yield pool
    finally:
        for table in (
            "funnel_interactions",
            "analytics_internal_audit",
            "billing_events_ledger",
            "subscription_snapshots",
            "user_identities",
        ):
            # Имена таблиц заданы только статическим списком выше.
            await pool.execute(f"DELETE FROM {table} WHERE internal_user_id LIKE 'test-funnel-%'")
        await pool.close()


async def identity(pool, suffix, index, start, internal=False):
    uid = f"test-funnel-{suffix}"
    await pool.execute(
        "INSERT INTO user_identities (internal_user_id, telegram_user_id, created_at, is_internal) VALUES ($1,$2,$3,$4)",
        uid,
        999000000000 + index,
        start,
        internal,
    )
    await pool.execute(
        "INSERT INTO subscription_snapshots (internal_user_id, state_label, trial_started_at, active_until_utc, plan_id) "
        "VALUES ($1,'active',$2,$2::timestamptz + interval '30 days','1m')",
        uid,
        start,
    )
    return uid


async def payment(
    pool, uid, ref, when, amount=16900, event="subscription_activated", provider="yookassa", status="accepted"
):
    await pool.execute(
        "INSERT INTO billing_events_ledger (internal_fact_ref,billing_provider_key,external_event_id,event_type,"
        "event_effective_at,event_received_at,internal_user_id,amount_minor_units,currency_code,status,ingestion_correlation_id)"
        " VALUES ($1,$2,$1,$3,$4,$4,$5,$6,'RUB',$7,'test-funnel')",
        ref,
        provider,
        event,
        when,
        uid,
        amount,
        status,
    )


async def test_mature_cohort_and_canonical_facts(pool):
    now = datetime.now(UTC)
    start = now - timedelta(days=10)
    cases = ["paid", "pending", "late", "before", "zero", "unknown", "topup", "grant", "internal", "crypto", "ignored"]
    for index, case in enumerate(cases):
        trial = now - timedelta(days=2) if case == "pending" else start
        uid = await identity(pool, case, index, trial, case == "internal")
        when = trial + timedelta(days=1)
        if case == "paid":
            when = trial
        if case == "late":
            when = trial + timedelta(days=10)
        if case == "before":
            when = trial - timedelta(seconds=1)
        await payment(
            pool,
            uid,
            f"grant:test-funnel-{case}" if case == "grant" else f"test-funnel-{case}",
            when,
            amount=0 if case == "zero" else None if case == "unknown" else 16900,
            event="device_topup" if case == "topup" else "subscription_activated",
            provider="cryptobot_v1" if case == "crypto" else "yookassa_v1",
            status="ignored" if case == "ignored" else "accepted",
        )
        if case in ("before", "paid"):
            await payment(pool, uid, f"test-funnel-{case}-repeat", trial + timedelta(days=2))
    result = await _mature_funnel(pool, now - timedelta(days=30), now)
    assert result == {
        "window_days": 10,
        "trials": 10,
        "mature_trials": 9,
        "pending_trials": 1,
        "converted_trials": 2,
        "trial_to_first_payment_rate": round(2 / 9, 4),
    }
    pending = await _mature_funnel(pool, now - timedelta(days=7), now)
    assert pending["mature_trials"] == 0
    assert pending["trial_to_first_payment_rate"] is None
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM funnel_events WHERE event_name='registered' AND internal_user_id LIKE 'test-funnel-%'"
        )
        == 11
    )


async def test_event_dedup_and_missing_identity(pool):
    uid = await identity(pool, "dedup", 20, datetime.now(UTC))
    args = {
        "event_name": "setup_started",
        "surface": "telegram",
        "dedup_key": "test-funnel-callback",
        "internal_user_id": uid,
    }
    await asyncio.gather(*(record_interaction(pool, **args) for _ in range(3)))
    await record_interaction(pool, **{**args, "internal_user_id": "missing", "dedup_key": "missing"})
    assert await pool.fetchval("SELECT count(*) FROM funnel_interactions WHERE internal_user_id=$1", uid) == 1


async def test_admin_internal_flag_security_audit_and_stats(pool, monkeypatch):
    for key, value in {
        "WEB_ADMIN_ENABLE": "1",
        "WEB_ADMIN_USERNAME": "admin",
        "WEB_ADMIN_PASSWORD": "s3cr3t-pass",
        "JWT_SECRET": "integration-jwt-secret",
        "WEB_API_DEV_INSECURE_COOKIE": "1",
    }.items():
        monkeypatch.setenv(key, value)
    now = datetime.now(UTC)
    uid = await identity(pool, "admin", 30, now - timedelta(days=12))
    await payment(pool, uid, "test-funnel-admin", now - timedelta(days=11))
    client, headers = await _login_client(pool)
    path = f"/api/v1/admin/users/{uid}/internal"
    try:
        before = (await client.get("/api/v1/admin/stats?period=30d")).json()
        assert (await client.post(path, json={"is_internal": True, "reason": "Тест"})).status_code == 403
        for body in ({"is_internal": "true", "reason": "Тест"}, {"is_internal": True, "reason": " "}, []):
            assert (await client.post(path, headers=headers, json=body)).status_code == 400
        for _ in range(2):
            assert (
                await client.post(path, headers=headers, json={"is_internal": True, "reason": "Тестовый аккаунт"})
            ).status_code == 200
        assert await pool.fetchval("SELECT count(*) FROM analytics_internal_audit WHERE internal_user_id=$1", uid) == 1
        after = (await client.get("/api/v1/admin/stats?period=30d")).json()
        assert after["kpi"]["revenue_rubles"] == before["kpi"]["revenue_rubles"] - 169
        assert after["kpi"]["new_users"] == before["kpi"]["new_users"] - 1
        assert after["kpi"]["active_subscriptions"] == before["kpi"]["active_subscriptions"] - 1
        assert after["kpi"]["trials_started"] == before["kpi"]["trials_started"] - 1
        assert after["kpi"]["paying_users"] == before["kpi"]["paying_users"] - 1
        assert after["funnel"]["mature_trials"] == before["funnel"]["mature_trials"] - 1
        assert (await client.get(f"/api/v1/admin/users/{uid}")).json()["user"]["is_internal"] is True
        assert (
            await client.post(path, headers=headers, json={"is_internal": False, "reason": "Отмена теста"})
        ).status_code == 200
        assert (await client.get("/api/v1/admin/stats?period=30d")).json()["funnel"] == before["funnel"]
        assert (
            await client.post(
                "/api/v1/admin/users/missing/internal", headers=headers, json={"is_internal": True, "reason": "Тест"}
            )
        ).status_code == 404
        client.cookies.clear()
        assert (
            await client.post(path, headers=headers, json={"is_internal": True, "reason": "Тест"})
        ).status_code == 401
    finally:
        await client.aclose()
