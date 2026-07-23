"""Интеграционные тесты операторского гранта дней подписки без смены тарифа.

POST /api/v1/admin/users/{internal_user_id}/add-days — PostgreSQL (opt-in через DATABASE_URL),
по образцу test_admin_promocodes_integration.py. Покрывает:
  - инвариант: plan_id / device_count НЕ меняются, активная подписка продлевается от текущего срока;
  - математика: expired/cancelled реактивируются от now;
  - «прочерк» (зарегистрирован, нет подписки) → 409 no_subscription_to_extend;
  - unknown user → 404; невалидные days → 400; без CSRF → 403; без auth → 401;
  - аудит admin_subscription_actions (бесплатный грант, не в billing).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from starlette.testclient import TestClient

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.app import build_web_api_app

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

# Уникальный диапазон telegram_user_id для этих тестов, чтобы не конфликтовать с прод-данными.
_TG_BASE = 999_000_001


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping admin add-days integration tests")
    return url


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_ENABLE", "1")
    monkeypatch.setenv("WEB_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD", "s3cr3t-pass")
    monkeypatch.setenv("JWT_SECRET", "integration-jwt-secret")
    monkeypatch.setenv("JWT_ADMIN_TTL_MINUTES", "60")
    monkeypatch.setenv("WEB_API_DEV_INSECURE_COOKIE", "1")


async def _make_user(
    pool: asyncpg.Pool,
    *,
    tg: int,
    state: str | None,
    plan_id: str = "1m",
    device_count: int = 2,
    active_until: datetime | None = None,
    keys_deactivated_at: datetime | None = None,
    keys_deleted_at: datetime | None = None,
) -> str:
    """Создаёт identity + (опц.) snapshot. Возвращает internal_user_id. state=None → без снапшота."""
    uid = f"add-days-test-{tg}"
    await pool.execute(
        "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2) "
        "ON CONFLICT (telegram_user_id) DO NOTHING",
        tg,
        uid,
    )
    if state is not None:
        await pool.execute(
            """INSERT INTO subscription_snapshots
               (internal_user_id, state_label, plan_id, device_count, active_until_utc,
                keys_deactivated_at, keys_deleted_at, updated_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,NOW())
               ON CONFLICT (internal_user_id) DO UPDATE SET
                 state_label = EXCLUDED.state_label,
                 plan_id = EXCLUDED.plan_id,
                 device_count = EXCLUDED.device_count,
                 active_until_utc = EXCLUDED.active_until_utc,
                 keys_deactivated_at = EXCLUDED.keys_deactivated_at,
                 keys_deleted_at = EXCLUDED.keys_deleted_at""",
            uid,
            state,
            plan_id,
            device_count,
            active_until,
            keys_deactivated_at,
            keys_deleted_at,
        )
    else:
        await pool.execute("DELETE FROM subscription_snapshots WHERE internal_user_id = $1", uid)
    return uid


async def _cleanup(pool: asyncpg.Pool) -> None:
    uids = [f"add-days-test-{_TG_BASE + i}" for i in range(10)]
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM admin_subscription_actions WHERE internal_user_id = ANY($1)", uids)
        await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id = ANY($1)", uids)
        await conn.execute("DELETE FROM user_identities WHERE internal_user_id = ANY($1)", uids)


def _login(client: TestClient) -> dict:
    r = client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "s3cr3t-pass"})
    assert r.status_code == 200, r.text
    return {"x-csrf-token": r.json()["csrf_token"]}


def test_admin_add_days(pg_url, admin_env):
    async def _run():
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=str(_MIGRATIONS_DIR))
            await _cleanup(pool)
            app = build_web_api_app(pool=pool)
            client = TestClient(app)
            h = _login(client)

            now = datetime.now(UTC)
            active_until = now + timedelta(days=5)

            # --- active user: extend from current expiry, plan/devices unchanged ---
            tg = _TG_BASE
            uid = await _make_user(pool, tg=tg, state="active", plan_id="1m", device_count=2, active_until=active_until)
            r = client.post(f"/api/v1/admin/users/{uid}/add-days", json={"days": 7}, headers=h)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["days_added"] == 7
            assert body["plan_unchanged"] == "1m"
            assert body["device_count_unchanged"] == 2
            # продление ровно на 7 дней от текущего срока
            new_until = datetime.fromisoformat(body["new_active_until"])
            assert abs((new_until - (active_until + timedelta(days=7))).total_seconds()) < 5

            snap = await pool.fetchrow(
                "SELECT state_label, plan_id, device_count, active_until_utc, "
                "keys_deactivated_at, keys_deleted_at FROM subscription_snapshots WHERE internal_user_id=$1",
                uid,
            )
            assert snap["state_label"] == "active"
            assert snap["plan_id"] == "1m"  # ИНВАРИАНТ: тариф не изменился
            assert snap["device_count"] == 2  # ИНВАРИАНТ: устройства не изменились
            assert snap["keys_deactivated_at"] is None
            assert snap["keys_deleted_at"] is None
            assert abs((snap["active_until_utc"] - (active_until + timedelta(days=7))).total_seconds()) < 5

            # провижининг отработал (stub → 5 серверов)
            assert body["servers_ok"] == 5
            assert body["provisioning"].startswith("success")

            # аудит записан (бесплатный грант, не в billing)
            audit = await pool.fetchrow(
                "SELECT actor, action, days, old_active_until, new_active_until, plan_id, device_count "
                "FROM admin_subscription_actions WHERE internal_user_id=$1 ORDER BY id DESC LIMIT 1",
                uid,
            )
            assert audit is not None
            assert audit["actor"] == "admin"
            assert audit["action"] == "add_days"
            assert audit["days"] == 7
            assert audit["plan_id"] == "1m"
            assert audit["device_count"] == 2

            # --- expired user: reactivate from now ---
            tg2 = _TG_BASE + 1
            uid2 = await _make_user(
                pool,
                tg=tg2,
                state="expired",
                active_until=now - timedelta(days=2),
                keys_deactivated_at=now - timedelta(days=2),
            )
            r = client.post(f"/api/v1/admin/users/{uid2}/add-days", json={"days": 10}, headers=h)
            assert r.status_code == 200, r.text
            snap2 = await pool.fetchrow(
                "SELECT state_label, active_until_utc, keys_deactivated_at FROM subscription_snapshots "
                "WHERE internal_user_id=$1",
                uid2,
            )
            assert snap2["state_label"] == "active"
            assert snap2["keys_deactivated_at"] is None
            # реактивация от now: срок ≈ now + 10 дней
            diff = (snap2["active_until_utc"] - now).total_seconds()
            assert timedelta(days=9).total_seconds() < diff < timedelta(days=10, hours=1).total_seconds()

            # --- cancelled user: reactivate from now ---
            tg3 = _TG_BASE + 2
            uid3 = await _make_user(pool, tg=tg3, state="cancelled", active_until=now - timedelta(days=1))
            r = client.post(f"/api/v1/admin/users/{uid3}/add-days", json={"days": 3}, headers=h)
            assert r.status_code == 200, r.text
            snap3 = await pool.fetchrow(
                "SELECT state_label, active_until_utc FROM subscription_snapshots WHERE internal_user_id=$1",
                uid3,
            )
            assert snap3["state_label"] == "active"
            assert (snap3["active_until_utc"] - now).total_seconds() > timedelta(days=2).total_seconds()

            # --- «прочерк»: registered, no snapshot → 409 ---
            tg4 = _TG_BASE + 3
            uid4 = await _make_user(pool, tg=tg4, state=None)
            r = client.post(f"/api/v1/admin/users/{uid4}/add-days", json={"days": 5}, headers=h)
            assert r.status_code == 409
            assert r.json()["error"] == "no_subscription_to_extend"

            # --- unknown user → 404 ---
            r = client.post("/api/v1/admin/users/does-not-exist/add-days", json={"days": 5}, headers=h)
            assert r.status_code == 404
            assert r.json()["error"] == "user_not_found"

            # --- invalid days ---
            for bad in (0, 4000, "abc", -5):
                r = client.post(f"/api/v1/admin/users/{uid}/add-days", json={"days": bad}, headers=h)
                assert r.status_code == 400, (bad, r.text)
                assert r.json()["error"] == "invalid_days"

            # --- missing days ---
            r = client.post(f"/api/v1/admin/users/{uid}/add-days", json={}, headers=h)
            assert r.status_code == 400
            assert r.json()["error"] == "invalid_days"

            # --- without CSRF header → 403 ---
            r = client.post(f"/api/v1/admin/users/{uid}/add-days", json={"days": 1})
            assert r.status_code == 403
            assert r.json()["error"] == "csrf_validation_failed"

            # --- without auth → 401 ---
            anon = TestClient(app)
            r = anon.post(f"/api/v1/admin/users/{uid}/add-days", json={"days": 1})
            assert r.status_code == 401

            # --- invariant under repeated grants: тариф остаётся прежним ---
            r = client.post(f"/api/v1/admin/users/{uid}/add-days", json={"days": 30}, headers=h)
            assert r.status_code == 200
            snap4 = await pool.fetchrow(
                "SELECT plan_id, device_count FROM subscription_snapshots WHERE internal_user_id=$1", uid
            )
            assert snap4["plan_id"] == "1m"
            assert snap4["device_count"] == 2
        finally:
            await _cleanup(pool)
            await pool.close()

    import asyncio

    asyncio.run(_run())
