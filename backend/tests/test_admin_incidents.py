"""Инциденты отделены от исходных проблем, защищены CSRF и версией записи."""

import asyncio
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.app import build_web_api_app


@pytest.mark.asyncio
async def test_incident_lifecycle_and_source_safety(monkeypatch):
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
    uid = "admin-incident-fixture"
    iid = uuid4()
    closing = None
    source_key = hashlib.sha256(f"rotation:{uid}".encode()).hexdigest()
    await pool.execute(
        "DELETE FROM admin_incident_events WHERE incident_id IN (SELECT id FROM admin_incidents WHERE source_key=$1)",
        source_key,
    )
    await pool.execute("DELETE FROM admin_incidents WHERE source_key=$1", source_key)
    try:
        await pool.execute(
            "INSERT INTO user_identities(internal_user_id,telegram_user_id,vless_rotation_pending) VALUES($1,9900700000,TRUE) ON CONFLICT(internal_user_id) DO UPDATE SET vless_rotation_pending=TRUE",
            uid,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/admin/incidents")).status_code == 401
            login = await client.post(
                "/api/v1/admin/auth/login", json={"username": "admin", "password": "test-password"}
            )
            headers = {"X-CSRF-Token": login.json()["csrf_token"]}
            response = await client.get("/api/v1/admin/incidents")
            assert response.status_code == 200, response.text
            issue = next(i for i in response.json()["issues"] if i["internal_user_id"] == uid)
            body = {"request_id": str(iid), "source_key": issue["key"], "title": "Ротация", "note": "Начата проверка"}
            assert (await client.post("/api/v1/admin/incidents", json=body)).status_code == 403
            created = await client.post("/api/v1/admin/incidents", json=body, headers=headers)
            assert created.status_code == 200, created.text
            assert (await client.post("/api/v1/admin/incidents", json=body, headers=headers)).json()[
                "duplicate"
            ] is True
            assert (
                await client.post("/api/v1/admin/incidents", json={**body, "note": "Другой запрос"}, headers=headers)
            ).status_code == 409
            url = f"/api/v1/admin/incidents/{iid}"
            assert (
                await client.post(url, json={"action": "resolve", "version": 1, "note": "Готово"}, headers=headers)
            ).json()["error"] == "condition_still_present"
            assert (await client.post(url, json={"action": "ack", "version": 1}, headers=headers)).status_code == 200
            assert (
                await client.post(url, json={"action": "note", "version": 1, "note": "Старая версия"}, headers=headers)
            ).status_code == 409
            assert (await client.post(url, json={"action": [], "version": 2}, headers=headers)).status_code == 400
            assert (
                await client.post(
                    url, json={"action": "note", "version": 2, "note": "Проверили журналы"}, headers=headers
                )
            ).status_code == 200
            assert (
                await pool.fetchval("SELECT vless_rotation_pending FROM user_identities WHERE internal_user_id=$1", uid)
                is True
            )
            await pool.execute("UPDATE user_identities SET vless_rotation_pending=FALSE WHERE internal_user_id=$1", uid)
            async with pool.acquire() as blocker:
                async with blocker.transaction():
                    await blocker.execute("SELECT id FROM admin_incidents WHERE id=$1 FOR UPDATE", iid)
                    closing = asyncio.create_task(
                        client.post(url, json={"action": "resolve", "version": 3, "note": "Готово"}, headers=headers)
                    )
                    async with asyncio.timeout(3):
                        while True:
                            await blocker.execute("SELECT pg_stat_clear_snapshot()")
                            if await blocker.fetchval(
                                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock')"
                            ):
                                break
                            await asyncio.sleep(0.01)
                    await blocker.execute(
                        "UPDATE user_identities SET vless_rotation_pending=TRUE WHERE internal_user_id=$1", uid
                    )
            assert (await closing).json()["error"] == "condition_still_present"
            await pool.execute("UPDATE user_identities SET vless_rotation_pending=FALSE WHERE internal_user_id=$1", uid)
            assert (await client.post("/api/v1/admin/incidents", json=body, headers=headers)).json()[
                "duplicate"
            ] is True

            assert (
                await client.post(
                    url, json={"action": "resolve", "version": 3, "note": "Синхронизация завершилась"}, headers=headers
                )
            ).status_code == 200
            result = (await client.get("/api/v1/admin/incidents")).json()
            record = next(i for i in result["incidents"] if i["id"] == str(iid))
            assert record["status"] == "resolved" and record["version"] == 4
            assert len([e for e in result["events"] if e["incident_id"] == str(iid)]) == 4
            assert "request_fingerprint" not in str(result)
    finally:
        if closing is not None and not closing.done():
            closing.cancel()
            await asyncio.gather(closing, return_exceptions=True)
        await pool.execute("DELETE FROM admin_incident_events WHERE incident_id=$1", iid)
        await pool.execute("DELETE FROM admin_incidents WHERE id=$1", iid)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", uid)
        await pool.close()
