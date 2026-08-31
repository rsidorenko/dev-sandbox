"""Юнит-тесты публичного каталога тарифов GET /api/v1/plans (без БД: pool=None)."""

from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from app.web_api.plans import handle_list_plans


@pytest.mark.asyncio
async def test_plans_catalog_defaults() -> None:
    app = Starlette(routes=[Route("/api/v1/plans", handle_list_plans, methods=["GET"])])
    app.state.pool = None  # провайдер без пула → код-дефолты, скидка выключена

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/plans")

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=30"
    body = resp.json()
    assert body["ok"] is True
    assert [p["plan_id"] for p in body["plans"]] == ["1d", "7d", "14d", "1m", "3m", "6m", "365d"]
    by_id = {p["plan_id"]: p for p in body["plans"]}
    assert by_id["1m"]["base_rubles"] == 249
    assert by_id["1m"]["duration_days"] == 30
    # Глобальная скидка не активна → null (фронт показывает цены без strikethrough).
    assert body["global_discount"] is None
    assert body["custom_day_price_rubles"] == 15
    assert body["extra_device_price_rubles"] == 80
    assert body["devices"] == {"base": 5, "max": 20}
