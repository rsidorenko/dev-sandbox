"""Интеграционный тест админ-эндпоинта /api/v1/admin/stats (PostgreSQL).

Opt-in через DATABASE_URL: реальные обработчики против БД с применением миграций.
Покрывает: 401 без auth; агрегаты выручки/пользователей/триалов/подписок за период;
фильтрацию status='accepted' (ignored не считается в выручке); валидацию period/tz;
бакеты группировки (hour/day/month); bare_xray-трафик (серия/по-серверам/KPI).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.app import build_web_api_app

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping admin stats integration tests")
    return url


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_ENABLE", "1")
    monkeypatch.setenv("WEB_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD", "s3cr3t-pass")
    monkeypatch.setenv("JWT_SECRET", "integration-jwt-secret")
    monkeypatch.setenv("JWT_ADMIN_TTL_MINUTES", "60")
    monkeypatch.setenv("WEB_API_DEV_INSECURE_COOKIE", "1")


# Идемпотентные тестовые префиксы — чистим до и после.
_FACT_REF = "test-stats-fact"
_U_PREFIX = "test-stats-u-"
_TG_BASE = 9_900_001_100  # вне реального диапазона telegram user id


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM billing_events_ledger "
            "WHERE internal_fact_ref LIKE 'test-stats-fact%' "
            "OR internal_fact_ref LIKE 'grant:test-stats-%' "
            "OR internal_fact_ref LIKE 'manual:test-stats-%'"
        )
        await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id LIKE 'test-stats-u-%'")
        await conn.execute("DELETE FROM user_identities WHERE internal_user_id LIKE 'test-stats-u-%'")
        await conn.execute("DELETE FROM bare_xray_traffic_daily WHERE server_id = 99001")


def _insert_ledger(
    conn: asyncpg.Connection,
    fact_ref: str,
    external_id: str,
    when: datetime,
    user_id: str,
    kopecks: int,
    status: str,
    provider_key: str = "yookassa",
) -> str:
    sql = (
        "INSERT INTO billing_events_ledger "
        "(internal_fact_ref, billing_provider_key, external_event_id, event_type, "
        " event_effective_at, event_received_at, internal_user_id, amount_minor_units, "
        " currency_code, status, ingestion_correlation_id) "
        "VALUES ($1,$7,$2,'payment',$3,$3,$4,$5,'RUB',$6,'cid-test-stats')"
    )
    return sql, (fact_ref, external_id, when, user_id, kopecks, status, provider_key)


async def _login_client(pool: asyncpg.Pool) -> tuple[httpx.AsyncClient, dict]:
    """Login через реальный эндпоинт → клиент с cookie-сессией + csrf-заголовок.

    Один event loop на пул и запросы: starlette TestClient гоняет ASGI в
    собственном лупе — пул из другого лупа роняет asyncpg («another operation
    is in progress»). Паттерн test_admin_plans_integration.
    """
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test")
    r = await client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "s3cr3t-pass"})
    assert r.status_code == 200, r.text
    return client, {"x-csrf-token": r.json()["csrf_token"]}


async def test_admin_stats_aggregates(pg_url, admin_env):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=4)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)

        now = datetime.now(UTC)
        recent = now - timedelta(hours=3)  # в окне 1d/7d/30d/90d
        old = now - timedelta(days=200)  # вне 7d/30d/90d, внутри 'all'

        u_recent, u_old = f"{_U_PREFIX}recent", f"{_U_PREFIX}old"
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_identities (telegram_user_id, internal_user_id, created_at) "
                "VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                _TG_BASE + 1,
                u_recent,
                recent,
            )
            await conn.execute(
                "INSERT INTO user_identities (telegram_user_id, internal_user_id, created_at) "
                "VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                _TG_BASE + 2,
                u_old,
                old,
            )
            # recent: активный 1m, триал недавно; old: state active, но active_until в прошлом (не «активный»).
            await conn.execute(
                "INSERT INTO subscription_snapshots "
                "(internal_user_id, state_label, plan_id, active_until_utc, trial_started_at) "
                "VALUES ($1,'active','1m',$2,$3) ON CONFLICT DO NOTHING",
                u_recent,
                now + timedelta(days=30),
                recent,
            )
            await conn.execute(
                "INSERT INTO subscription_snapshots "
                "(internal_user_id, state_label, plan_id, active_until_utc, trial_started_at) "
                "VALUES ($1,'active','3m',$2,$3) ON CONFLICT DO NOTHING",
                u_old,
                now - timedelta(days=1),
                old,
            )

            # Accepted-факт 1000.00 RUB — реальный платёж (провайдер yookassa).
            sql, args = _insert_ledger(conn, f"{_FACT_REF}-acc", "ext-acc-1", recent, u_recent, 100000, "accepted")
            await conn.execute(sql, *args)
            # Ignored-факт 9999.99 — НЕ должен войти в выручку (статус).
            sql, args = _insert_ledger(conn, f"{_FACT_REF}-ign", "ext-ign-1", recent, u_recent, 999999, "ignored")
            await conn.execute(sql, *args)
            # Грант оператором через grant_subscription.py: fact_ref 'grant:...', провайдер 'yookassa' — НЕ должен войти.
            sql, args = _insert_ledger(
                conn, "grant:test-stats-grant-yk", "grant-ext-1", recent, u_recent, 50000, "accepted"
            )
            await conn.execute(sql, *args)
            # Ручной грант: provider_key='manual_grant' — НЕ должен войти.
            sql, args = _insert_ledger(
                conn,
                "manual:test-stats-grant-mg",
                "manual-ext-1",
                recent,
                u_recent,
                30000,
                "accepted",
                provider_key="manual_grant",
            )
            await conn.execute(sql, *args)
            # bare_xray-трафик: 1 ГБ up + 1 ГБ down на тестовом server_id 99001
            # (в vpn_servers его нет → label должен упасть в 'server 99001').
            await conn.execute(
                "INSERT INTO bare_xray_traffic_daily "
                "(stat_date, server_id, email, uplink_bytes, downlink_bytes) "
                "VALUES (CURRENT_DATE, 99001, 'user-traffictest', 1073741824, 1073741824) "
                "ON CONFLICT (stat_date, server_id, email) DO UPDATE SET "
                "uplink_bytes = EXCLUDED.uplink_bytes, downlink_bytes = EXCLUDED.downlink_bytes"
            )

        client, h = await _login_client(pool)

        # 401 без auth (отдельный клиент без cookie-сессии)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        ) as anon:
            assert (await anon.get("/api/v1/admin/stats?period=7d")).status_code == 401

        # period=7d — основной сценарий
        r = await client.get("/api/v1/admin/stats?period=7d", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["period"]["key"] == "7d"
        assert body["period"]["bucket"] == "day"
        # выручка = только реальный accepted 1000.00: ignored отсечён статусом,
        # оба гранта (grant:… под yookassa и manual_grant) — исключены как внутренние.
        assert body["kpi"]["revenue_rubles"] == 1000.0
        assert body["kpi"]["payments_count"] == 1
        assert body["kpi"]["paying_users"] == 1
        # в окне 7d — только недавний юзер/триал
        assert body["kpi"]["new_users"] == 1
        assert body["kpi"]["trials_started"] == 1
        # recent активный (active_until в будущем); old — нет (active_until в прошлом)
        assert body["kpi"]["active_subscriptions"] == 1
        assert any(p["plan_id"] == "1m" and p["count"] == 1 for p in body["by_plan"])
        assert all(p["plan_id"] != "3m" for p in body["by_plan"])
        # recent стартанул триал и имеет accepted-факт → конверсия 1.0
        assert body["kpi"]["trial_to_paid_rate"] == 1.0
        # временные ряды сформированы
        assert len(body["series"]["revenue"]) >= 1
        assert len(body["series"]["new_users"]) >= 1
        assert len(body["series"]["trials"]) >= 1
        assert body["series"]["revenue"][0]["rubles"] == 1000.0
        # bare_xray-трафик: точка 2.0 ГБ (1 up + 1 down) в серии, один юзер,
        # в by_server — тестовый server_id без vpn_servers-строки. Дату не сверяем:
        # CURRENT_DATE у PG в UTC, окно — в MSK (уязвимо только на границе суток).
        assert any(abs(p["gb"] - 2.0) < 0.01 for p in body["series"]["traffic"])
        assert body["kpi"]["traffic_gb"] >= 2.0
        assert body["kpi"]["traffic_users"] >= 1
        assert any(s["label"] == "server 99001" and abs(s["gb"] - 2.0) < 0.01 for s in body["traffic_by_server"])

        # period=1d — бакет hour
        r = await client.get("/api/v1/admin/stats?period=1d", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["period"]["bucket"] == "hour"

        # period=all — бакет month, видны оба юзера (окно без нижней границы)
        r = await client.get("/api/v1/admin/stats?period=all", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["period"]["bucket"] == "month"
        assert r.json()["kpi"]["new_users"] == 2

        # невалидный period → 400
        assert (await client.get("/api/v1/admin/stats?period=bogus", headers=h)).status_code == 400
        # невалидный tz → 400
        assert (await client.get("/api/v1/admin/stats?period=7d&tz=Not/AZone", headers=h)).status_code == 400
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()
