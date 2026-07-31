"""Интеграционные тесты рассылки против PostgreSQL (opt-in через DATABASE_URL).

Покрывает:
  * SQL-резолвер аудитории — корректность счёта по категориям, дедуп UNION,
    исключение web-only (telegram_user_id < 0), явные цели (tg-id и email).
  * HTTP-обвязку: auth (401 без куки), CSRF (403 без токена), валидацию send
    (503 без BOT_TOKEN, 400 invalid_html, 400 no_recipients), list/status.

Фоновый BroadcastRunner покрыт юнит-тестами на фейках (test_admin_broadcast_unit.py).
Админ/web_api-тесты НЕ входят в postgres-smoke CI — этот файл прогоняется только локально.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
from starlette.testclient import TestClient

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.app import build_web_api_app

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

# Тестовые telegram id (9900000xx) — не пересекаются с прод-данными.
_TG = {
    "active_paid": 990000001,
    "active_trial": 990000002,
    "exp_0_2": 990000003,
    "exp_3_5": 990000004,
    "exp_6_7": 990000005,
    "exp_8_30": 990000006,
    "exp_30plus": 990000007,
    "dormant": 990000008,
    "web_only": -990000009,
}
_ALL_TG = list(_TG.values())


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping broadcast integration tests")
    return url


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_ENABLE", "1")
    monkeypatch.setenv("WEB_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD", "s3cr3t-pass")
    monkeypatch.setenv("JWT_SECRET", "integration-jwt-secret")
    monkeypatch.setenv("JWT_ADMIN_TTL_MINUTES", "60")
    monkeypatch.setenv("WEB_API_DEV_INSECURE_COOKIE", "1")


async def _insert_fixtures(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        # Активная платная подписка.
        await c.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id, trial_used) "
            "VALUES ($1,'u'||$1, TRUE) ON CONFLICT (telegram_user_id) DO NOTHING",
            _TG["active_paid"],
        )
        await c.execute(
            "INSERT INTO subscription_snapshots (internal_user_id, state_label, plan_id, active_until_utc) "
            "VALUES ('u'||$1, 'active', '1m', now() + interval '30 days') "
            "ON CONFLICT (internal_user_id) DO NOTHING",
            _TG["active_paid"],
        )
        # Активный триал (plan_id IS NULL, trial_expires_at > now).
        await c.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id, trial_used) "
            "VALUES ($1,'u'||$1, TRUE) ON CONFLICT (telegram_user_id) DO NOTHING",
            _TG["active_trial"],
        )
        await c.execute(
            "INSERT INTO subscription_snapshots (internal_user_id, state_label, plan_id, "
            "trial_started_at, trial_expires_at) "
            "VALUES ('u'||$1, 'active', NULL, now(), now() + interval '2 days') "
            "ON CONFLICT (internal_user_id) DO NOTHING",
            _TG["active_trial"],
        )
        # Диапазоны истечения (state_label не важен — предикат игнорирует его).
        for key, days in (("exp_0_2", 1), ("exp_3_5", 4), ("exp_6_7", 7), ("exp_8_30", 15), ("exp_30plus", 40)):
            tg = _TG[key]
            await c.execute(
                "INSERT INTO user_identities (telegram_user_id, internal_user_id, trial_used) "
                "VALUES ($1,'u'||$1, TRUE) ON CONFLICT (telegram_user_id) DO NOTHING",
                tg,
            )
            await c.execute(
                "INSERT INTO subscription_snapshots (internal_user_id, state_label, plan_id, active_until_utc) "
                "VALUES ('u'||$1, 'expired', '1m', now() - ($2 || ' days')::interval) "
                "ON CONFLICT (internal_user_id) DO NOTHING",
                tg,
                str(days),
            )
        # Спящий: trial_used=FALSE, без снапшота и без billing.
        await c.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id, trial_used) "
            "VALUES ($1,'u'||$1, FALSE) ON CONFLICT (telegram_user_id) DO NOTHING",
            _TG["dormant"],
        )
        # Web-only (отрицательный telegram_user_id) — НЕ должен попасть ни в одну категорию.
        await c.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id) "
            "VALUES ($1,'u'||$1) ON CONFLICT (telegram_user_id) DO NOTHING",
            _TG["web_only"],
        )
        # Email для теста резолва явной цели по почте.
        await c.execute(
            "INSERT INTO user_emails (telegram_user_id, email, is_verified) "
            "VALUES ($1, 'broadcast-test-1@example.test', TRUE) ON CONFLICT DO NOTHING",
            _TG["active_paid"],
        )


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        await c.execute("DELETE FROM user_emails WHERE telegram_user_id = ANY($1::bigint[])", _ALL_TG)
        await c.execute(
            "DELETE FROM subscription_snapshots WHERE internal_user_id IN "
            "(SELECT ('u'||tg)::text FROM unnest($1::bigint[]) AS tg)",
            _ALL_TG,
        )
        await c.execute("DELETE FROM user_identities WHERE telegram_user_id = ANY($1::bigint[])", _ALL_TG)
        await c.execute("DELETE FROM broadcast_jobs WHERE created_by = 'admin'")


class _StubClient:
    async def send_text_message(self, **_kw) -> int:
        return 1


def test_broadcast_estimate_and_handler_wiring(pg_url, admin_env):
    async def _run():
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            app = build_web_api_app(pool=pool)
            client = TestClient(app)

            r = client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "s3cr3t-pass"})
            assert r.status_code == 200, r.text
            csrf = r.json()["csrf_token"]
            h = {"x-csrf-token": csrf}

            def estimate(categories, targets=None):
                body = {"categories": categories}
                if targets is not None:
                    body["explicit_targets"] = targets
                return client.post("/api/v1/admin/broadcast/estimate", json=body, headers=h)

            # --- Базовые счета ДО вставки фикстур ---
            cats = ["all", "active", "expired_0_2d", "expired_3_5d", "expired_6_7d", "expired_8_30d",
                    "expired_30plus", "dormant"]
            baseline = {c: estimate([c]).json()["total"] for c in cats}
            baseline_all = baseline["all"]

            await _insert_fixtures(pool)

            # --- Дельты по категориям (каждая фикстура попадает ровно в одну категорию) ---
            expected_delta = {
                "all": 8,  # 9 фикстур, web-only (tg<0) исключён
                "active": 2,
                "expired_0_2d": 1,
                "expired_3_5d": 1,
                "expired_6_7d": 1,
                "expired_8_30d": 1,
                "expired_30plus": 1,
                "dormant": 1,
            }
            for c in cats:
                got = estimate([c]).json()["total"]
                assert got == baseline[c] + expected_delta[c], f"category {c}: {baseline[c]}->{got}"

            # --- Дедуп: active ⊆ all → объединение не больше all ---
            r = estimate(["all", "active"])
            assert r.json()["total"] == baseline_all + 8, "active должен быть подмножеством all (дедуп)"

            # --- Web-only исключён из all ---
            assert estimate(["all"]).json()["total"] == baseline_all + 8

            # --- Явные цели: tg-id ---
            r = estimate([], targets=[str(_TG["exp_0_2"]), str(_TG["dormant"])])
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 2

            # --- Явные цели: email (резолв через user_emails) ---
            r = estimate([], targets=["broadcast-test-1@example.test"])
            assert r.json()["total"] == 1
            assert _TG["active_paid"] in r.json()["explicit_resolved"]

            # --- Неразрешённая цель ---
            r = estimate([], targets=["no-such-user-xyz@example.test"])
            assert r.json()["total"] == 0
            assert r.json()["explicit_unresolved"] == ["no-such-user-xyz@example.test"]

            # --- invalid_categories ---
            r = estimate(["bogus"])
            assert r.status_code == 400
            assert r.json()["error"] == "invalid_categories"

            # --- auth: без куки → 401 ---
            anon = TestClient(app)
            assert anon.post("/api/v1/admin/broadcast/estimate", json={"categories": ["all"]}).status_code == 401

            # --- CSRF: send без X-CSRF-Token → 403 (под залогиненной сессией) ---
            r = client.post("/api/v1/admin/broadcast/send", json={"categories": ["all"], "message_text": "x"})
            assert r.status_code == 403
            assert r.json()["error"] == "csrf_validation_failed"

            # --- send без BOT_TOKEN → 503 telegram_not_configured (client is None) ---
            assert app.state.telegram_raw_client is None
            r = client.post(
                "/api/v1/admin/broadcast/send",
                json={"categories": [], "explicit_targets": [str(_TG["active_paid"])], "message_text": "<b>hi</b>"},
                headers=h,
            )
            assert r.status_code == 503
            assert r.json()["error"] == "telegram_not_configured"

            # --- инжект фейк-клиента для проверки синхронных валидаций send ---
            app.state.telegram_raw_client = _StubClient()

            r = client.post(
                "/api/v1/admin/broadcast/send",
                json={"categories": [], "explicit_targets": [str(_TG["active_paid"])], "message_text": "<b>unclosed"},
                headers=h,
            )
            assert r.status_code == 400
            assert r.json()["error"] == "invalid_html"

            r = client.post(
                "/api/v1/admin/broadcast/send",
                json={"categories": [], "explicit_targets": ["definitely-absent@example.test"], "message_text": "ok"},
                headers=h,
            )
            assert r.status_code == 400
            assert r.json()["error"] == "no_recipients"

            # --- list/status ---
            r = client.get("/api/v1/admin/broadcast/jobs?limit=5")
            assert r.status_code == 200
            assert "jobs" in r.json()
            r = client.get("/api/v1/admin/broadcast/jobs/00000000-0000-0000-0000-000000000000")
            assert r.status_code == 404
            assert r.json()["error"] == "job_not_found"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
