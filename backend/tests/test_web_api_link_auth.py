"""Ссылки выдаются на экране; email не принимается."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

import app.web_api.link_auth as link_auth
from app.web_api.app import build_web_api_app


@pytest.fixture
def client(monkeypatch):
    for name in ("XUI_ENABLED", "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    link_auth._rate_limit_store.clear()
    pool = AsyncMock()
    pool.fetchrow.side_effect = lambda sql, uid, token, ref: {
        "subscription_token": token,
        "expires_at": datetime.now(UTC) + timedelta(hours=48),
    }
    return TestClient(build_web_api_app(pool=pool)), pool


def test_link_shown_and_not_cached(client):
    web, pool = client
    first = web.post("/api/v1/auth/link/request", json={})
    second = web.post("/api/v1/auth/link/request", json={})
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert "/sub/" in first.json()["subscription_url"]
    assert first.json()["subscription_url"] != second.json()["subscription_url"]
    assert len(first.json()["subscription_url"].rsplit("/", 1)[1]) >= 32
    assert 47.9 < (datetime.fromisoformat(first.json()["expires_at"]) - datetime.now(UTC)).total_seconds() / 3600 <= 48
    for call in pool.fetchrow.call_args_list:
        assert "INSERT INTO pending_web_links" in call.args[0]
    pool.execute.assert_not_called()


@pytest.mark.parametrize("payload", [{"email": "user@example.org"}, {"next": "/dashboard"}, [], None])
def test_reject_old_email_contract(client, payload):
    web, pool = client
    assert web.post("/api/v1/auth/link/request", json=payload).status_code == 400
    pool.fetchrow.assert_not_called()


def test_rate_limit(client):
    web, pool = client
    for _ in range(5):
        assert web.post("/api/v1/auth/link/request", json={}).status_code == 200
    assert web.post("/api/v1/auth/link/request", json={}).status_code == 429
    assert pool.fetchrow.call_count == 5


def test_referral_kept_only_on_temporary_record(client):
    web, pool = client
    assert web.post("/api/v1/auth/link/request", json={"referral_code": "abc123"}).status_code == 200
    assert pool.fetchrow.call_args.args[-1] == "abc123"
    pool.execute.assert_not_called()
