"""Мониторинг различает ошибку, устаревание, отсутствие и успешное наблюдение."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.admin_servers import observation_status
from app.web_api.app import build_web_api_app


@pytest.mark.parametrize(
    ("status", "age", "expected"),
    [
        ("success", 0, "healthy"),
        ("error", 0, "error"),
        ("success", 901, "stale"),
        ("error", 901, "stale"),
        (None, None, "unknown"),
    ],
)
def test_observation_status(status, age, expected):
    now = datetime.now(UTC)
    checked = now - timedelta(seconds=age) if age is not None else None
    assert observation_status(status, checked, now=now, stale_after=900) == expected


@pytest.mark.asyncio
async def test_server_api_safe_fields_and_empty_observations(monkeypatch):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("Нужен изолированный PostgreSQL")
    for key, value in {
        "WEB_ADMIN_ENABLE": "1",
        "WEB_ADMIN_USERNAME": "admin",
        "WEB_ADMIN_PASSWORD": "test-password",
        "JWT_SECRET": "test-admin-secret",
        "WEB_API_DEV_INSECURE_COOKIE": "1",
    }.items():
        monkeypatch.setenv(key, value)
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
    await pool.execute("DELETE FROM vpn_servers WHERE id=991001")
    try:
        await pool.execute(
            "INSERT INTO vpn_servers(id,label,country_code,country_flag,server_host,panel_url,panel_username,panel_password) VALUES(991001,'Тест','DE','','example.test','secret-panel-canary','secret-login-canary','secret-password-canary')"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/admin/servers")).status_code == 401
            assert (
                await client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "test-password"})
            ).status_code == 200
            result = await client.get("/api/v1/admin/servers")
            assert result.status_code == 200
            assert "canary" not in result.text
            server = next(s for s in result.json()["servers"] if s["id"] == 991001)
            assert server["health"] == "unknown" and server["checked_at"] is None
            await pool.execute(
                "INSERT INTO vpn_server_observations(server_id,status,checked_at,last_success_at) VALUES(991001,'success',NOW(),NOW())"
            )
            result = await client.get("/api/v1/admin/servers")
            assert next(s for s in result.json()["servers"] if s["id"] == 991001)["health"] == "healthy"
            await pool.execute("UPDATE vpn_server_observations SET status='error' WHERE server_id=991001")
            result = await client.get("/api/v1/admin/servers")
            server = next(s for s in result.json()["servers"] if s["id"] == 991001)
            assert server["health"] == "error" and server["last_success_at"] is not None
    finally:
        await pool.execute("DELETE FROM vpn_servers WHERE id=991001")
        await pool.close()
