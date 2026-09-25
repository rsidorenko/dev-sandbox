"""Срезы кабинета: исходные суммы, валидация и повторный импорт."""

import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.admin_ad_snapshots import snapshot_json, validate_snapshot
from app.web_api.app import build_web_api_app

ROOT = Path(__file__).resolve().parents[2]


def payload():
    return json.loads((ROOT / "docs/analytics/telegram-ads-2026-09-25.json").read_text())


def test_screenshot_totals_and_ambiguous_lines_preserved():
    data = validate_snapshot(payload())
    report = snapshot_json({"payload": json.dumps(data), "created_at": datetime.now(UTC)})
    assert len(data["placements"]) == 20
    assert data["observed_date"] is None
    assert data["placements"][8]["spent_ton_lines"] == ["0.60", "0.00"]
    assert data["placements"][8]["budget_ton_lines"] == ["6.40", "1.00"]
    assert report["totals"] == {
        "views": 136970,
        "clicks": 1164,
        "actions": 208,
        "spent_ton": "79.35",
        "spent_usd": "106.33",
    }


@pytest.mark.parametrize("value", ["NaN", "-1", "1e2", 1.34, True, "9999999999"])
def test_snapshot_rejects_unsafe_money(value):
    data = payload()
    data["usd_per_ton"] = value
    with pytest.raises(ValueError):
        validate_snapshot(data)


def test_snapshot_rejects_duplicate_and_invalid_count():
    data = payload()
    data["placements"].append(copy.deepcopy(data["placements"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_snapshot(data)
    data = payload()
    data["placements"][0]["views"] = True
    with pytest.raises(ValueError, match="count"):
        validate_snapshot(data)


async def test_snapshot_import_authenticated_idempotent_and_conflict(monkeypatch):
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("Нужна тестовая PostgreSQL")
    for key, value in {
        "WEB_ADMIN_ENABLE": "1",
        "WEB_ADMIN_USERNAME": "admin",
        "WEB_ADMIN_PASSWORD": "snapshot-test",
        "JWT_SECRET": "snapshot-test-jwt-secret",
        "WEB_API_DEV_INSECURE_COOKIE": "1",
    }.items():
        monkeypatch.setenv(key, value)
    pool = await asyncpg.create_pool(url)
    data = payload()
    data["source_ref"] = "test-ad-snapshot"
    try:
        await apply_postgres_migrations(pool, migrations_directory=ROOT / "backend/migrations")
        await apply_postgres_migrations(pool, migrations_directory=ROOT / "backend/migrations")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        ) as c:
            path = "/api/v1/admin/telegram-ads/snapshots"
            assert (await c.get(path)).status_code == 401
            assert (await c.post(path, json=data)).status_code == 401
            login = await c.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "snapshot-test"})
            headers = {"x-csrf-token": login.json()["csrf_token"]}
            assert (await c.post(path, json=data)).status_code == 403
            first = (await c.post(path, json=data, headers=headers)).json()
            second = (await c.post(path, json=data, headers=headers)).json()
            assert first["created"] is True
            assert second["created"] is False
            assert first["snapshot"] == second["snapshot"]
            data["usd_per_ton"] = "2"
            assert (await c.post(path, json=data, headers=headers)).status_code == 409
            assert (await c.get(path)).json()["snapshot"]["usd_per_ton"] == "1.34"
    finally:
        await pool.execute("DELETE FROM telegram_ads_snapshots WHERE source_ref = 'test-ad-snapshot'")
        await pool.close()
