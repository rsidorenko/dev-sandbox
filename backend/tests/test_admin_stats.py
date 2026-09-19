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
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.admin_stats import (
    _completed_through_date,
    _compute_window,
    _funnel_diagnostics_v2,
    _trial_feedback_stats,
)
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


def test_custom_window_uses_inclusive_local_dates_and_caps_today_at_now() -> None:
    now = datetime(2026, 9, 19, 12, 30, tzinfo=UTC)
    historical = _compute_window(
        "custom",
        "Europe/Moscow",
        date_from_raw="2026-09-01",
        date_to_raw="2026-09-03",
        now=now,
    )
    assert historical.from_ts == datetime(2026, 8, 31, 21, tzinfo=UTC)
    assert historical.to_ts == datetime(2026, 9, 3, 21, tzinfo=UTC)
    assert historical.trunc == "day"

    today = _compute_window(
        "custom",
        "Europe/Moscow",
        date_from_raw="2026-09-19",
        date_to_raw="2026-09-19",
        now=now,
    )
    assert today.to_ts == now
    assert today.trunc == "hour"


@pytest.mark.parametrize(
    ("date_from", "date_to"),
    [
        (None, None),
        ("2026-09-03", "2026-09-01"),
        ("2026-09-01", "2026-09-20"),
        ("2025-09-18", "2026-09-19"),
        ("01.09.2026", "2026-09-19"),
    ],
)
def test_custom_window_rejects_invalid_ranges(date_from: str | None, date_to: str | None) -> None:
    with pytest.raises(ValueError):
        _compute_window(
            "custom",
            "Europe/Moscow",
            date_from_raw=date_from,
            date_to_raw=date_to,
            now=datetime(2026, 9, 19, 12, 30, tzinfo=UTC),
        )


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM billing_events_ledger "
            "WHERE internal_fact_ref LIKE 'test-stats-fact%' "
            "OR internal_fact_ref LIKE 'grant:test-stats-%' "
            "OR internal_fact_ref LIKE 'manual:test-stats-%'"
        )
        await conn.execute("DELETE FROM experiment_assignments WHERE internal_user_id LIKE 'test-stats-u-%'")
        await conn.execute("DELETE FROM trial_feedback WHERE internal_user_id LIKE 'test-stats-u-%'")
        await conn.execute("DELETE FROM funnel_interactions WHERE internal_user_id LIKE 'test-stats-u-%'")
        await conn.execute("DELETE FROM telegram_ads_attributions WHERE internal_user_id LIKE 'test-stats-u-%'")
        await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id LIKE 'test-stats-u-%'")
        await conn.execute("DELETE FROM user_identities WHERE internal_user_id LIKE 'test-stats-u-%'")
        await conn.execute("DELETE FROM bare_xray_traffic_daily WHERE server_id BETWEEN 99001 AND 99099")


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
        "VALUES ($1,$7,$2,'subscription_activated',$3,$3,$4,$5,'RUB',$6,'cid-test-stats')"
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

        u_recent, u_old, u_nakrut = f"{_U_PREFIX}recent", f"{_U_PREFIX}old", f"{_U_PREFIX}nakrut"
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
            await conn.execute(
                "INSERT INTO user_identities (telegram_user_id, internal_user_id, created_at) "
                "VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                _TG_BASE + 3,
                u_nakrut,
                recent,
            )
            await conn.execute(
                "INSERT INTO telegram_ads_attributions "
                "(internal_user_id, campaign_key, telegram_update_id, attributed_at) VALUES ($1,'nakrut',$2,$3)",
                u_nakrut,
                _TG_BASE + 3,
                recent,
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
            await conn.execute(
                "INSERT INTO subscription_snapshots "
                "(internal_user_id, state_label, plan_id, active_until_utc, trial_started_at) "
                "VALUES ($1,'active','1m',$2,$3) ON CONFLICT DO NOTHING",
                u_nakrut,
                now + timedelta(days=30),
                recent,
            )

            # Accepted-факт 1000.00 RUB — реальный платёж (провайдер yookassa).
            sql, args = _insert_ledger(conn, f"{_FACT_REF}-acc", "ext-acc-1", recent, u_recent, 100000, "accepted")
            await conn.execute(sql, *args)
            sql, args = _insert_ledger(
                conn,
                f"{_FACT_REF}-nakrut",
                "ext-nakrut-1",
                recent,
                u_nakrut,
                900000,
                "accepted",
            )
            await conn.execute(sql, *args)
            assigned_at = recent - timedelta(minutes=10)
            await conn.execute(
                "INSERT INTO experiment_assignments (internal_user_id, experiment_key, variant, assigned_at) "
                "VALUES ($1, 'checkout_reassurance_v1', 'reassurance', $2)",
                u_recent,
                assigned_at,
            )
            await conn.execute(
                "INSERT INTO funnel_interactions "
                "(internal_user_id, event_name, surface, platform, dedup_key, occurred_at, plan_id) "
                "VALUES ($1, 'payment_created', 'web', '', 'test-stats-payment-created', $2, '1m')",
                u_recent,
                recent - timedelta(minutes=1),
            )
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
                "VALUES ($1, 99001, $2, 1073741824, 1073741824) "
                "ON CONFLICT (stat_date, server_id, email) DO UPDATE SET "
                "uplink_bytes = EXCLUDED.uplink_bytes, downlink_bytes = EXCLUDED.downlink_bytes",
                recent.astimezone(ZoneInfo("Europe/Moscow")).date(),
                f"user-{u_recent[:16]}",
            )
            await conn.execute(
                "INSERT INTO bare_xray_traffic_daily "
                "(stat_date, server_id, email, uplink_bytes, downlink_bytes) "
                "VALUES ($1, 99001, $2, 10737418240, 10737418240) "
                "ON CONFLICT (stat_date, server_id, email) DO UPDATE SET "
                "uplink_bytes = EXCLUDED.uplink_bytes, downlink_bytes = EXCLUDED.downlink_bytes",
                recent.astimezone(ZoneInfo("Europe/Moscow")).date(),
                f"user-{u_nakrut[:16]}",
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
        assert body["period"]["date_to"] == now.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
        # выручка = только реальный accepted 1000.00: ignored отсечён статусом,
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
        assert body["product_funnel"]["registered_users"] >= 1
        assert body["product_funnel"]["payment_created_users"] >= 1
        assert body["product_funnel"]["payment_succeeded_users"] >= 1
        assert body["funnel_diagnostics_v2"]["meaningful_traffic_threshold_bytes"] == 1_048_576
        experiment = next(
            item
            for item in body["experiments"]
            if item["experiment_key"] == "checkout_reassurance_v1" and item["variant"] == "reassurance"
        )
        assert experiment["exposures"] == 1
        assert experiment["payment_created_users"] == 1
        assert experiment["paid_users"] == 1
        assert experiment["paid_rate"] == 1.0
        assert experiment["revenue_rubles"] == 1000.0
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
        assert all(campaign["campaign_key"] != "nakrut" for campaign in body["telegram_ads"]["campaigns"])
        # Исходные данные nakrut сохраняются: фильтр действует только в аналитике.
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM telegram_ads_attributions WHERE internal_user_id=$1 AND campaign_key='nakrut'",
                u_nakrut,
            )
            == 1
        )

        # period=1d — бакет hour
        r = await client.get("/api/v1/admin/stats?period=1d", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["period"]["bucket"] == "hour"

        # period=all — бакет month, видны оба юзера (окно без нижней границы)
        r = await client.get("/api/v1/admin/stats?period=all", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["period"]["bucket"] == "month"
        assert r.json()["kpi"]["new_users"] == 2

        # Произвольный период — обе локальные даты включительны, но nakrut не считается.
        recent_date = recent.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
        r = await client.get(
            f"/api/v1/admin/stats?period=custom&date_from={recent_date}&date_to={recent_date}",
            headers=h,
        )
        assert r.status_code == 200, r.text
        custom = r.json()
        assert custom["period"]["key"] == "custom"
        assert custom["period"]["date_from"] == recent_date
        assert custom["period"]["date_to"] == recent_date
        assert custom["period"]["bucket"] == "hour"
        assert custom["kpi"]["new_users"] == 1
        assert custom["kpi"]["revenue_rubles"] == 1000.0

        old_date = old.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
        r = await client.get(
            f"/api/v1/admin/stats?period=custom&date_from={old_date}&date_to={old_date}",
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert r.json()["kpi"]["new_users"] == 1
        # Текущий gauge подписок не превращаем в исторический snapshot.
        assert r.json()["kpi"]["active_subscriptions"] == 1

        # невалидный period → 400
        assert (await client.get("/api/v1/admin/stats?period=bogus", headers=h)).status_code == 400
        # невалидный tz → 400
        assert (await client.get("/api/v1/admin/stats?period=7d&tz=Not/AZone", headers=h)).status_code == 400
        today = now.astimezone(ZoneInfo("Europe/Moscow")).date()
        too_old = (today - timedelta(days=366)).isoformat()
        for query in (
            "period=custom",
            "period=custom&date_from=2026-09-10&date_to=2026-09-01",
            "period=custom&date_from=2026-09-01&date_to=2999-01-01",
            f"period=custom&date_from={too_old}&date_to={today.isoformat()}",
        ):
            response = await client.get(f"/api/v1/admin/stats?{query}", headers=h)
            assert response.status_code == 400, response.text
            assert response.json()["error"] == "invalid_date_range"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_funnel_diagnostics_meaningful_retention_and_routes(pg_url):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=4)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)

        now = datetime(2026, 9, 19, 12, tzinfo=UTC)
        cases = {
            "direct": datetime(2026, 9, 14, 12, tzinfo=UTC),
            "guided": datetime(2026, 9, 15, 12, tzinfo=UTC),
            "noise": datetime(2026, 9, 15, 14, tzinfo=UTC),
            "recent": datetime(2026, 9, 19, 10, tzinfo=UTC),
        }
        diag_ids = {name: f"{_U_PREFIX}{name[:2]}-diag" for name in cases}
        async with pool.acquire() as conn:
            for index, (name, started_at) in enumerate(cases.items(), start=20):
                internal_user_id = diag_ids[name]
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id, created_at) VALUES ($1,$2,$3)",
                    _TG_BASE + index,
                    internal_user_id,
                    started_at,
                )
                await conn.execute(
                    "INSERT INTO subscription_snapshots "
                    "(internal_user_id,state_label,trial_started_at,trial_expires_at,active_until_utc) "
                    "VALUES ($1,'active',$2,$2::timestamptz + interval '3 days',"
                    "$2::timestamptz + interval '3 days')",
                    internal_user_id,
                    started_at,
                )
                await conn.execute(
                    "INSERT INTO funnel_interactions "
                    "(internal_user_id,event_name,surface,platform,dedup_key,occurred_at,connection_route) "
                    "VALUES ($1,'platform_selected','telegram','ios',$2,$3,'direct')",
                    internal_user_id,
                    f"diag-{name}-direct",
                    started_at + timedelta(minutes=10),
                )

            guided_user = diag_ids["guided"]
            await conn.execute(
                "INSERT INTO funnel_interactions "
                "(internal_user_id,event_name,surface,platform,dedup_key,occurred_at,connection_route) "
                "VALUES ($1,'platform_selected','telegram','ios','diag-guided-guided',$2,'guided')",
                guided_user,
                cases["guided"] + timedelta(minutes=20),
            )

            traffic_rows = [
                ("direct", 0, 2 * 1024 * 1024),
                ("direct", 1, 2 * 1024 * 1024),
                ("direct", 2, 512 * 1024),
                ("direct", 3, 2 * 1024 * 1024),
                ("guided", 0, 2 * 1024 * 1024),
                ("guided", 2, 2 * 1024 * 1024),
                ("noise", 0, 100),
                ("recent", 0, 2 * 1024 * 1024),
            ]
            for server_offset, (name, day_offset, bytes_total) in enumerate(traffic_rows):
                internal_user_id = diag_ids[name]
                trial_date = cases[name].astimezone(ZoneInfo("Europe/Moscow")).date()
                await conn.execute(
                    "INSERT INTO bare_xray_traffic_daily "
                    "(stat_date,server_id,email,uplink_bytes,downlink_bytes) VALUES ($1,$2,$3,$4,0)",
                    trial_date + timedelta(days=day_offset),
                    99001 + server_offset,
                    f"user-{internal_user_id[:16]}",
                    bytes_total,
                )

        window = _compute_window(
            "custom",
            "Europe/Moscow",
            date_from_raw="2026-09-12",
            date_to_raw="2026-09-19",
            now=now,
        )
        result = await _funnel_diagnostics_v2(
            pool,
            window,
            _completed_through_date(window, "Europe/Moscow"),
        )
        assert result == {
            "meaningful_traffic_threshold_bytes": 1_048_576,
            "trials": 4,
            "first_traffic_users": 4,
            "meaningful_traffic_users": 3,
            "trial_to_meaningful_traffic_rate": 0.75,
            "retention": {
                "d1": {"eligible_users": 3, "retained_users": 1, "rate": 0.3333},
                "d2": {"eligible_users": 3, "retained_users": 1, "rate": 0.3333},
                "d3": {"eligible_users": 3, "retained_users": 1, "rate": 0.3333},
            },
            "routes": {
                "direct": {
                    "started_users": 4,
                    "meaningful_traffic_users": 2,
                    "meaningful_traffic_rate": 0.5,
                },
                "guided": {
                    "started_users": 1,
                    "meaningful_traffic_users": 1,
                    "meaningful_traffic_rate": 1.0,
                },
                "guided_after_direct_users": 1,
            },
        }
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_trial_feedback_stats_excludes_internal_and_nakrut_users(pg_url):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=4)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        now = datetime.now(UTC)
        users = [
            (f"{_U_PREFIX}feedback-1", _TG_BASE + 41, False),
            (f"{_U_PREFIX}feedback-2", _TG_BASE + 42, False),
            (f"{_U_PREFIX}feedback-internal", _TG_BASE + 43, True),
            (f"{_U_PREFIX}feedback-nakrut", _TG_BASE + 44, False),
        ]
        async with pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO user_identities "
                "(internal_user_id,telegram_user_id,is_internal,created_at) VALUES ($1,$2,$3,$4)",
                [(uid, telegram_id, internal, now - timedelta(days=1)) for uid, telegram_id, internal in users],
            )
            await conn.execute(
                "INSERT INTO telegram_ads_attributions "
                "(internal_user_id,campaign_key,telegram_update_id,attributed_at) VALUES ($1,'nakrut',944,$2)",
                users[3][0],
                now - timedelta(days=1),
            )
            feedback_rows = [
                (users[0][0], "connection_problem", "connect_failed", "ios", "direct"),
                (users[1][0], "connection_problem", "too_hard", "android", "guided"),
                (users[0][0], "trial_nonpayment", "too_expensive", "", ""),
                (users[2][0], "trial_nonpayment", "other", "", ""),
                (users[3][0], "trial_nonpayment", "no_need", "", ""),
            ]
            await conn.executemany(
                "INSERT INTO trial_feedback "
                "(internal_user_id,feedback_type,reason_code,platform,connection_route,updated_at) "
                "VALUES ($1,$2,$3,$4,$5,$6)",
                [(*row, now - timedelta(hours=1)) for row in feedback_rows],
            )

        result = await _trial_feedback_stats(pool, now - timedelta(days=7), now)
        assert result["connection_problem"] == {
            "respondents": 2,
            "reasons": [
                {"code": "connect_failed", "users": 1},
                {"code": "too_hard", "users": 1},
            ],
            "platforms": [
                {"code": "android", "users": 1},
                {"code": "ios", "users": 1},
            ],
            "routes": [
                {"code": "direct", "users": 1},
                {"code": "guided", "users": 1},
            ],
        }
        assert result["trial_nonpayment"] == {
            "respondents": 1,
            "reasons": [{"code": "too_expensive", "users": 1}],
            "platforms": [],
            "routes": [],
        }
    finally:
        await _cleanup(pool)
        await pool.close()
