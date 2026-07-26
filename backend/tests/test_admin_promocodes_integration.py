"""Интеграционные тесты админ-эндпоинтов промокодов и пользователей (PostgreSQL).

Opt-in через DATABASE_URL: реальные обработчики против БД с применением миграций.
Покрывает: login → CRUD промокодов (create/list/update/duplicate/invalid/delete) + список пользователей.
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest
from starlette.testclient import TestClient

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.app import build_web_api_app

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

_CODE = "TESTPROMO123"


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping admin integration tests")
    return url


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_ENABLE", "1")
    monkeypatch.setenv("WEB_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD", "s3cr3t-pass")
    monkeypatch.setenv("JWT_SECRET", "integration-jwt-secret")
    monkeypatch.setenv("JWT_ADMIN_TTL_MINUTES", "60")
    monkeypatch.setenv("WEB_API_DEV_INSECURE_COOKIE", "1")


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM promo_codes WHERE code = $1", _CODE)
        await conn.execute("DELETE FROM promo_redemptions WHERE code = $1", _CODE)


def test_admin_promocode_crud_and_users(pg_url, admin_env):
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

            # create
            r = client.post(
                "/api/v1/admin/promocodes",
                json={
                    "code": _CODE,
                    "audience": "new_users",
                    "discount_kind": "percent",
                    "discount_value": 20,
                },
                headers=h,
            )
            assert r.status_code == 201, r.text
            assert r.json()["promocode"]["discount_value"] == 20
            assert r.json()["promocode"]["created_by"] == "admin"

            # duplicate -> 409
            r = client.post(
                "/api/v1/admin/promocodes",
                json={"code": _CODE, "audience": "new_users", "discount_kind": "percent", "discount_value": 10},
                headers=h,
            )
            assert r.status_code == 409

            # invalid percent -> 400
            r = client.post(
                "/api/v1/admin/promocodes",
                json={"code": "BADPERCENT", "audience": "new_users", "discount_kind": "percent", "discount_value": 150},
                headers=h,
            )
            assert r.status_code == 400

            # list contains our code
            r = client.get("/api/v1/admin/promocodes")
            assert r.status_code == 200
            codes = [p["code"] for p in r.json()["promocodes"]]
            assert _CODE in codes

            # update
            r = client.patch(
                f"/api/v1/admin/promocodes/{_CODE}", json={"discount_value": 25, "is_active": False}, headers=h
            )
            assert r.status_code == 200, r.text
            assert r.json()["promocode"]["discount_value"] == 25
            assert r.json()["promocode"]["is_active"] is False

            # per-tariff: create + PATCH с discount_value=null (регресс: ранее 400 invalid_discount_value,
            # т.к. ветка discount_value реагировала на наличие ключа, а не на значение).
            pt_code = "TESTPTPERPLAN"
            await pool.execute("DELETE FROM promo_codes WHERE code = $1", pt_code)
            r = client.post(
                "/api/v1/admin/promocodes",
                json={
                    "code": pt_code,
                    "audience": "new_users",
                    "discount_kind": "fixed_rubles",
                    "discount_value": None,
                    "discounts_by_plan": {"1d": 1, "1m": 69, "365d": 349},
                },
                headers=h,
            )
            assert r.status_code == 201, r.text
            assert r.json()["promocode"]["discounts_by_plan"] == {"1d": 1, "1m": 69, "365d": 349}

            r = client.patch(
                f"/api/v1/admin/promocodes/{pt_code}",
                json={
                    "audience": "new_users",
                    "discount_kind": "fixed_rubles",
                    "discount_value": None,
                    "discounts_by_plan": {"1d": 1, "7d": 24, "14d": 49, "1m": 69, "3m": 119, "6m": 259, "365d": 349},
                    "is_active": True,
                },
                headers=h,
            )
            assert r.status_code == 200, r.text
            assert len(r.json()["promocode"]["discounts_by_plan"]) == 7
            await pool.execute("DELETE FROM promo_codes WHERE code = $1", pt_code)

            # delete
            r = client.delete(f"/api/v1/admin/promocodes/{_CODE}", headers=h)
            assert r.status_code == 200
            r = client.get("/api/v1/admin/promocodes")
            assert _CODE not in [p["code"] for p in r.json()["promocodes"]]

            # users list works
            r = client.get("/api/v1/admin/users?limit=5")
            assert r.status_code == 200
            assert "users" in r.json() and "total" in r.json()

            # auth required without cookie
            client2 = TestClient(app)
            assert client2.get("/api/v1/admin/promocodes").status_code == 401
        finally:
            await _cleanup(pool)
            await pool.close()

    import asyncio

    asyncio.run(_run())
