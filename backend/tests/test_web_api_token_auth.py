"""Тесты Bearer-авторизации по subscription-токену (link-auth; email-логин срезан).

Инварианты:
- валидный токен → identity (claims) без entitlement-гейта: истёкшая подписка
  видит профиль и может продлиться;
- Bearer с JWT-формой (с точками) — сразу 401 без похода в БД (subscription-токены
  точек не содержат);
- мутации без CSRF-заголовка проходят (кук нет — CSRF-вектора нет);
- неудачные попытки (неизвестный/мусорный токен) rate-limit-ятся per-IP.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import app.web_api.middleware as middleware
from app.web_api.app import _with_auth, build_web_api_app

_TOKEN = "AbCdEfGh1234567890123456"  # dot-free urlsafe — форма prod-токена
_TG_ID = -42
_INTERNAL_ID = "web_deadbeefcafe"
_UNSET: Any = object()  # сентинел: отличить «не передано» от явного None (неизвестный токен)


class _TokenAuthFakePool:
    """asyncpg-подобный пул: токен-lookup + profile-JOIN по готовым данным."""

    def __init__(
        self,
        *,
        token_row: dict[str, Any] | None,
        profile_row: dict[str, Any] | None,
    ) -> None:
        self.token_row = token_row
        self.profile_row = profile_row
        self.token_lookups = 0

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "WHERE subscription_token = $1" in sql:
            self.token_lookups += 1
            return self.token_row
        if "FROM user_identities i" in sql:
            return self.profile_row
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetchval(self, sql: str, *args: Any) -> Any:
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def execute(self, sql: str, *args: Any) -> str:
        raise AssertionError(f"unexpected execute: {sql}")

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        raise AssertionError(f"unexpected fetch: {sql}")


def _profile_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "internal_user_id": _INTERNAL_ID,
        "subscription_token": _TOKEN,
        "trial_used": False,
        "state_label": "active",
        "active_until_utc": datetime.now(UTC) + timedelta(days=10),
        "plan_id": "1m",
        "device_count": 5,
        "trial_started_at": None,
        "trial_expires_at": None,
        "issuance_state": "issued",
        "referral_code": None,
        "balance_kopecks": None,
        "referral_count": 0,
    }
    base.update(overrides)
    return base


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token_row: dict[str, Any] | None | object = _UNSET,
    profile_row: dict[str, Any] | None | object = _UNSET,
) -> tuple[TestClient, _TokenAuthFakePool]:
    for var in ("XUI_ENABLED", "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    middleware._token_auth_fail_store.clear()
    if token_row is _UNSET:
        token_row = {"telegram_user_id": _TG_ID, "internal_user_id": _INTERNAL_ID}
    if profile_row is _UNSET:
        profile_row = _profile_row()
    pool = _TokenAuthFakePool(
        token_row=token_row,  # type: ignore[arg-type]
        profile_row=profile_row,  # type: ignore[arg-type]
    )
    app = build_web_api_app(pool=pool)  # type: ignore[arg-type]
    return TestClient(app), pool


def test_bearer_token_returns_profile_with_login_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    client, pool = _make_client(monkeypatch)
    r = client.get("/api/v1/user/profile", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["telegram_user_id"] == _TG_ID
    assert body["user"]["login_url"].endswith(f"/auth/{_TOKEN}")
    assert body["user"]["subscription_url"].endswith(f"/sub/{_TOKEN}")
    assert pool.token_lookups == 1  # идемпотентность: внутренний require_auth не ходит в БД второй раз


def test_unknown_token_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _make_client(monkeypatch, token_row=None)
    r = client.get("/api/v1/user/profile", headers={"Authorization": "Bearer no_such_token_x1"})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_expired_subscription_still_authenticates(monkeypatch: pytest.MonkeyPatch) -> None:
    # без entitlement-гейта: истёкшая подписка видит кабинет (CTA продления)
    client, _ = _make_client(
        monkeypatch,
        profile_row=_profile_row(state_label="expired", active_until_utc=datetime.now(UTC) - timedelta(days=1)),
    )
    r = client.get("/api/v1/user/profile", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200
    assert r.json()["subscription"]["state"] == "expired"


def test_jwt_shaped_bearer_rejected_without_db(monkeypatch: pytest.MonkeyPatch) -> None:
    # subscription-токены не содержат точек: JWT-подобный Bearer — 401 без похода в БД
    client, pool = _make_client(monkeypatch)
    r = client.get("/api/v1/user/profile", headers={"Authorization": "Bearer aaa.bbb.ccc"})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"
    assert pool.token_lookups == 0


def test_bearer_token_mutation_without_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    # Кук нет → CSRF-вектора нет: мутация идёт по Bearer без csrf-заголовков
    for var in ("XUI_ENABLED", "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    middleware._token_auth_fail_store.clear()

    async def _dummy(request):
        return JSONResponse({"ok": True, "tg": request.state.user["telegram_user_id"]})

    pool = _TokenAuthFakePool(
        token_row={"telegram_user_id": _TG_ID, "internal_user_id": _INTERNAL_ID},
        profile_row=_profile_row(),
    )
    mini = Starlette(routes=[Route("/mutate", _with_auth(_dummy), methods=["POST"])])
    mini.state.pool = pool  # type: ignore[assignment]
    client = TestClient(mini)
    r = client.post("/mutate", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200
    assert r.json()["tg"] == _TG_ID


def test_token_auth_failures_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _make_client(monkeypatch, token_row=None)
    for _ in range(middleware._TOKEN_AUTH_FAIL_MAX):
        r = client.get("/api/v1/user/profile", headers={"Authorization": "Bearer wrong_token_a"})
        assert r.status_code == 401
    r_next = client.get("/api/v1/user/profile", headers={"Authorization": "Bearer wrong_token_b"})
    assert r_next.status_code == 429
