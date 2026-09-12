"""Контракты ручного расхода и экономики Telegram Ads."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_telegram_ads_spend import PostgresTelegramAdsSpendRepository
from app.web_api.admin_ad_spend import (
    _normalize_campaign_key,
    _parse_amount_kopecks,
    _parse_spend_date,
)
from app.web_api.admin_stats import _telegram_ads_economics, _telegram_ads_funnel
from app.web_api.app import build_web_api_app

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"
_CAMPAIGN = "test_economics_repo"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("apk_01", "apk_01"),
        (" ADS_APK_01 ", "apk_01"),
        ("tgads_apk_01", "apk_01"),
        ("bad.dot", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_campaign_key(raw, expected):
    assert _normalize_campaign_key(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0),
        ("10", 1_000),
        ("10.01", 1_001),
        ("10,50", 1_050),
        ("0.001", None),
        ("-1", None),
        ("NaN", None),
        ("1e2", None),
        ("+1", None),
        (1.5, None),
        ("100000000.01", None),
    ],
)
def test_parse_amount_kopecks_without_float(raw, expected):
    assert _parse_amount_kopecks(raw) == expected


def test_parse_spend_date_rejects_invalid_boundaries():
    assert _parse_spend_date("2025-01-01") == date(2025, 1, 1)
    assert _parse_spend_date("2024-12-31") is None
    assert _parse_spend_date("2999-01-01") is None
    assert _parse_spend_date("not-a-date") is None


def test_economics_summary_preserves_zero_denominators():
    summary = _telegram_ads_economics(
        [
            {
                "spend_rubles": 1500.0,
                "revenue_rubles": 338.0,
                "attributed_users": 2,
                "trials_started": 2,
                "acquired_users": 1,
            }
        ]
    )
    assert summary == {
        "spend_rubles": 1500.0,
        "revenue_rubles": 338.0,
        "attributed_users": 2,
        "trials_started": 2,
        "acquired_users": 1,
        "cost_per_attributed_user_rubles": 750.0,
        "cost_per_trial_rubles": 750.0,
        "cac_rubles": 1500.0,
        "roas": round(338 / 1500, 4),
    }
    assert _telegram_ads_economics([]) == {
        "spend_rubles": 0,
        "revenue_rubles": 0,
        "attributed_users": 0,
        "trials_started": 0,
        "acquired_users": 0,
        "cost_per_attributed_user_rubles": None,
        "cost_per_trial_rubles": None,
        "cac_rubles": None,
        "roas": None,
    }


def test_telegram_ads_funnel_preserves_all_zero_metrics():
    assert _telegram_ads_funnel(None) == {
        "attributed_users": 0,
        "trials_started": 0,
        "setup_started_users": 0,
        "platform_selected_users": 0,
        "first_vpn_traffic_users": 0,
        "connection_confirmed_users": 0,
        "connection_problem_users": 0,
        "checkout_opened_users": 0,
        "payment_created_users": 0,
        "mature_trials": 0,
        "pending_trials": 0,
        "first_payment_users": 0,
        "second_payment_users": 0,
        "acquired_users": 0,
        "platform_users": {"ios": 0, "android": 0, "mac": 0, "win": 0, "tv": 0},
    }


@pytest.fixture
async def pool():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip("Нужна отдельная тестовая PostgreSQL DATABASE_URL")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
    await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
    try:
        yield pool
    finally:
        await pool.execute("DELETE FROM telegram_ads_spend_actions WHERE campaign_key = $1", _CAMPAIGN)
        await pool.execute("DELETE FROM telegram_ads_spend_daily WHERE campaign_key = $1", _CAMPAIGN)
        await pool.close()


async def test_spend_repository_is_state_idempotent_and_audited(pool):
    repository = PostgresTelegramAdsSpendRepository(pool)
    spend_date = date(2026, 9, 1)

    created = await repository.set_daily_spend(
        campaign_key=_CAMPAIGN,
        spend_date=spend_date,
        amount_kopecks=10_001,
        actor="admin:test",
    )
    repeated = await repository.set_daily_spend(
        campaign_key=_CAMPAIGN,
        spend_date=spend_date,
        amount_kopecks=10_001,
        actor="admin:other",
    )
    assert created["changed"] is True
    assert repeated["changed"] is False
    assert repeated["updated_at"] == created["updated_at"]
    assert repeated["updated_by"] == "admin:test"
    assert (
        await pool.fetchval("SELECT count(*) FROM telegram_ads_spend_actions WHERE campaign_key = $1", _CAMPAIGN) == 1
    )

    updated = await repository.set_daily_spend(
        campaign_key=_CAMPAIGN,
        spend_date=spend_date,
        amount_kopecks=20_000,
        actor="admin:test",
    )
    assert updated["changed"] is True
    assert (
        await pool.fetchval("SELECT count(*) FROM telegram_ads_spend_actions WHERE campaign_key = $1", _CAMPAIGN) == 2
    )

    cleared = await repository.set_daily_spend(
        campaign_key=_CAMPAIGN,
        spend_date=spend_date,
        amount_kopecks=0,
        actor="admin:test",
    )
    repeated_clear = await repository.set_daily_spend(
        campaign_key=_CAMPAIGN,
        spend_date=spend_date,
        amount_kopecks=0,
        actor="admin:test",
    )
    assert cleared["changed"] is True
    assert repeated_clear["changed"] is False
    assert (
        await pool.fetchval("SELECT count(*) FROM telegram_ads_spend_actions WHERE campaign_key = $1", _CAMPAIGN) == 3
    )
    assert await pool.fetchval("SELECT count(*) FROM telegram_ads_spend_daily WHERE campaign_key = $1", _CAMPAIGN) == 0


async def test_spend_endpoint_requires_admin_and_csrf(pool, monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_ENABLE", "1")
    monkeypatch.setenv("WEB_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD", "s3cr3t-pass")
    monkeypatch.setenv("JWT_SECRET", "test-economics-jwt-secret")
    monkeypatch.setenv("JWT_ADMIN_TTL_MINUTES", "60")
    monkeypatch.setenv("WEB_API_DEV_INSECURE_COOKIE", "1")
    payload = {
        "campaign_key": "TGADS_TEST_ECONOMICS_REPO",
        "spend_date": "2026-09-01",
        "amount_rubles": "12.34",
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)),
        base_url="http://test",
    ) as client:
        assert (await client.put("/api/v1/admin/telegram-ads/spend", json=payload)).status_code == 401
        login = await client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "s3cr3t-pass"},
        )
        assert login.status_code == 200
        assert (await client.put("/api/v1/admin/telegram-ads/spend", json=payload)).status_code == 403

        headers = {"x-csrf-token": login.json()["csrf_token"]}
        response = await client.put("/api/v1/admin/telegram-ads/spend", json=payload, headers=headers)
        assert response.status_code == 200
        spend = response.json()["spend"]
        assert spend == {
            "campaign_key": _CAMPAIGN,
            "spend_date": "2026-09-01",
            "amount_kopecks": 1_234,
            "amount_rubles": "12.34",
            "updated_by": "admin",
            "updated_at": spend["updated_at"],
            "changed": True,
        }

        payload["amount_rubles"] = "0"
        cleared = await client.put("/api/v1/admin/telegram-ads/spend", json=payload, headers=headers)
        assert cleared.status_code == 200
        assert cleared.json()["spend"]["amount_kopecks"] == 0
