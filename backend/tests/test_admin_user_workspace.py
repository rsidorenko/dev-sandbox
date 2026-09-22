"""Карточка поддержки: реальные SQL, доступ администратора и защита секретов."""

import os
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.app import build_web_api_app


@pytest.mark.asyncio
async def test_user_workspace(monkeypatch):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("Нужен изолированный PostgreSQL")
    for key, value in {
        "WEB_ADMIN_ENABLE": "1",
        "WEB_ADMIN_USERNAME": "admin",
        "WEB_ADMIN_PASSWORD": "workspace-test-password",
        "JWT_SECRET": "workspace-test-secret",
        "WEB_API_DEV_INSECURE_COOKIE": "1",
    }.items():
        monkeypatch.setenv(key, value)
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
    prefix = "workspace-fixture-"
    ids = [prefix + str(i) for i in range(1003)]

    async def cleanup():
        await pool.execute("DELETE FROM subscription_snapshots WHERE internal_user_id=ANY($1)", ids)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=ANY($1)", ids)

    await cleanup()
    try:
        await pool.executemany(
            "INSERT INTO user_identities(internal_user_id,telegram_user_id,subscription_token) VALUES($1,$2,$3)",
            [(uid, 9900900000 + i, "secret-canary-" + str(i)) for i, uid in enumerate(ids)],
        )
        await pool.execute(
            "INSERT INTO subscription_snapshots(internal_user_id,state_label,trial_expires_at,updated_at) "
            "VALUES($1,'active',NOW()+INTERVAL '2 days',NOW()),($2,'active',NOW()+INTERVAL '2 days',NOW())",
            ids[0],
            ids[1],
        )
        await pool.execute("UPDATE subscription_snapshots SET keys_deleted_at=NOW() WHERE internal_user_id=$1", ids[1])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/admin/users")).status_code == 401
            assert (await client.get(f"/api/v1/admin/users/{ids[0]}")).status_code == 401
            login = await client.post(
                "/api/v1/admin/auth/login", json={"username": "admin", "password": "workspace-test-password"}
            )
            assert login.status_code == 200
            result = await client.get("/api/v1/admin/users", params={"search": prefix, "limit": 2, "offset": 1000})
            assert result.status_code == 200, result.text
            body = result.json()
            assert body["total"] == 1003 and len(body["users"]) == 2
            assert sum(v["count"] for v in body["facets"]["status"]) == 1003
            assert "secret-canary" not in result.text
            filtered = (await client.get("/api/v1/admin/users", params={"search": prefix, "status": "active"})).json()
            assert filtered["total"] == 1
            assert filtered["users"][0]["internal_user_id"] == ids[0]
            assert filtered["users"][0]["is_active"] is True
            assert filtered["users"][0]["remaining_days"] >= 1
            assert sum(v["count"] for v in filtered["facets"]["status"]) == 1003
            assert (await client.get("/api/v1/admin/users", params={"search": "' OR 1=1 --"})).json()["total"] == 0
            combined = (
                await client.get(
                    "/api/v1/admin/users",
                    params={"search": prefix, "status": "active", "plan": "none", "balance": "0", "kind": "regular"},
                )
            ).json()
            assert combined["total"] == 1
            assert combined["facets"]["balance"] == [{"key": "0", "count": 1}]
            await pool.executemany(
                "INSERT INTO balance_access_jobs(internal_user_id,operation_key,status,created_at) "
                "VALUES($1,$2,$3,NOW()+$4*INTERVAL '1 second')",
                [(ids[0], f"fixture-{i}", "pending" if i == 0 else "done", i) for i in range(22)],
            )
            detail = await client.get(f"/api/v1/admin/users/{ids[0]}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["diagnostics"]["live_access"] == "not_checked"
            assert detail.json()["diagnostics"]["pending_access_jobs"] == 1
            assert all(j["status"] == "done" for j in detail.json()["diagnostics"]["access_jobs"])
            assert "secret-canary" not in detail.text
            assert (await client.get("/api/v1/admin/users/does-not-exist")).status_code == 404
    finally:
        await cleanup()
        await pool.close()
