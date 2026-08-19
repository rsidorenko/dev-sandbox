"""Интеграционные тесты админ-эндпоинтов промокодов и пользователей (PostgreSQL).

Opt-in через DATABASE_URL: реальные обработчики против БД с применением миграций.
Покрывает: login → CRUD промокодов (create/list/update/duplicate/invalid/delete) + список пользователей.

Тесты — асинхронные, через httpx.AsyncClient + ASGITransport на едином event-loop:
общий пул и ASGI-приложение живут на одном loop (pytest-asyncio auto). Раньше
использовался sync Starlette TestClient поверх asyncio.run(main()) — пул создавался
на одном loop, а запрос гнался на другом (anyio portal) → asyncpg «Future attached
to a different loop». Cookie сессии ставит сам login-ответ (Set-Cookie).
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import httpx
import pytest

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


async def _login_client(pool) -> tuple[httpx.AsyncClient, dict]:
    """Login через реальный эндпоинт → клиент с cookie-сессией + csrf-заголовок."""

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test")
    r = await client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "s3cr3t-pass"})
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    return client, {"x-csrf-token": csrf}


async def test_admin_promocode_crud_and_users(pg_url, admin_env):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        client, h = await _login_client(pool)

        # create
        r = await client.post(
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
        r = await client.post(
            "/api/v1/admin/promocodes",
            json={"code": _CODE, "audience": "new_users", "discount_kind": "percent", "discount_value": 10},
            headers=h,
        )
        assert r.status_code == 409

        # invalid percent -> 400
        r = await client.post(
            "/api/v1/admin/promocodes",
            json={"code": "BADPERCENT", "audience": "new_users", "discount_kind": "percent", "discount_value": 150},
            headers=h,
        )
        assert r.status_code == 400

        # list contains our code
        r = await client.get("/api/v1/admin/promocodes")
        assert r.status_code == 200
        codes = [p["code"] for p in r.json()["promocodes"]]
        assert _CODE in codes

        # update
        r = await client.patch(
            f"/api/v1/admin/promocodes/{_CODE}", json={"discount_value": 25, "is_active": False}, headers=h
        )
        assert r.status_code == 200, r.text
        assert r.json()["promocode"]["discount_value"] == 25
        assert r.json()["promocode"]["is_active"] is False

        # per-tariff: create + PATCH с discount_value=null (регресс: ранее 400 invalid_discount_value,
        # т.к. ветка discount_value реагировала на наличие ключа, а не на значение).
        pt_code = "TESTPTPERPLAN"
        await pool.execute("DELETE FROM promo_codes WHERE code = $1", pt_code)
        r = await client.post(
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

        r = await client.patch(
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

        # audience=all_users: создание сохраняет категорию «все пользователи»
        # (регресс CHECK-ограничения audience — ранее БД отвергала значение).
        all_code = "TESTPALLALL"
        await pool.execute("DELETE FROM promo_codes WHERE code = $1", all_code)
        r = await client.post(
            "/api/v1/admin/promocodes",
            json={
                "code": all_code,
                "audience": "all_users",
                "discount_kind": "percent",
                "discount_value": 15,
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        assert r.json()["promocode"]["audience"] == "all_users"
        # список отдаёт код с новой аудиторией.
        r = await client.get("/api/v1/admin/promocodes")
        assert all_code in [p["code"] for p in r.json()["promocodes"]]
        await pool.execute("DELETE FROM promo_codes WHERE code = $1", all_code)

        # delete
        r = await client.delete(f"/api/v1/admin/promocodes/{_CODE}", headers=h)
        assert r.status_code == 200
        r = await client.get("/api/v1/admin/promocodes")
        assert _CODE not in [p["code"] for p in r.json()["promocodes"]]

        # users list works
        r = await client.get("/api/v1/admin/users?limit=5")
        assert r.status_code == 200
        assert "users" in r.json() and "total" in r.json()

        # auth required without cookie
        anon = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        )
        assert (await anon.get("/api/v1/admin/promocodes")).status_code == 401
        await anon.aclose()
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_admin_promocode_per_user_limit_roundtrip(pg_url, admin_env):
    """max_redemptions_per_user: create=1 → PATCH null («неограничено») → PATCH 1;
    0/отрицательное на create и update → 400 invalid_max_redemptions_per_user."""

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        client, h = await _login_client(pool)

        # невалидное значение на create → 400 (код не создаётся)
        r = await client.post(
            "/api/v1/admin/promocodes",
            json={
                "code": _CODE,
                "audience": "all_users",
                "discount_kind": "percent",
                "discount_value": 10,
                "max_redemptions_per_user": 0,
            },
            headers=h,
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"] == "invalid_max_redemptions_per_user"

        # create с «единожды»
        r = await client.post(
            "/api/v1/admin/promocodes",
            json={
                "code": _CODE,
                "audience": "all_users",
                "discount_kind": "percent",
                "discount_value": 10,
                "max_redemptions_per_user": 1,
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        assert r.json()["promocode"]["max_redemptions_per_user"] == 1

        # PATCH → «неограничено» (null) и обратно
        r = await client.patch(f"/api/v1/admin/promocodes/{_CODE}", json={"max_redemptions_per_user": None}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["promocode"]["max_redemptions_per_user"] is None
        r = await client.patch(f"/api/v1/admin/promocodes/{_CODE}", json={"max_redemptions_per_user": 1}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["promocode"]["max_redemptions_per_user"] == 1

        # невалидное значение на update → 400
        for bad in (0, -1):
            r = await client.patch(
                f"/api/v1/admin/promocodes/{_CODE}", json={"max_redemptions_per_user": bad}, headers=h
            )
            assert r.status_code == 400, r.text
            assert r.json()["error"] == "invalid_max_redemptions_per_user"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()
