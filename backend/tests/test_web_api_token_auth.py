"""Тесты Bearer-авторизации по subscription-токену (dual-mode, MR2 link-auth).

Инварианты:
- валидный токен → identity (claims) без entitlement-гейта: истёкшая подписка
  видит профиль и может продлиться;
- Bearer-заголовок с JWT-формой — по-прежнему legacy-JWT-путь (переходный период);
- CSRF не требуется, когда креденшл идёт заголовком Authorization (double-submit
  остаётся обязательным для cookie-аутентификации);
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
from app.web_api.app import _with_auth, _with_csrf, build_web_api_app
from app.web_api.auth import _issue_jwt
from app.web_api.middleware import generate_csrf_token

_TOKEN = "AbCdEfGh1234567890123456"  # dot-free urlsafe — форма prod-токена
_TG_ID = -42
_INTERNAL_ID = "web_deadbeefcafe"
_JWT_SECRET = "token-auth-test-secret"
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
        if "jwt_revocation_list" in sql:
            return None  # legacy-JWT не отозван
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
    monkeypatch.setenv("JWT_SECRET", _JWT_SECRET)
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


def test_legacy_jwt_bearer_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    client, pool = _make_client(monkeypatch)
    jwt = _issue_jwt({"telegram_user_id": _TG_ID, "internal_user_id": _INTERNAL_ID, "email": None})
    r = client.get("/api/v1/user/profile", headers={"Authorization": f"Bearer {jwt}"})
    assert r.status_code == 200
    assert pool.token_lookups == 0  # JWT-форма не трогает token-lookup


def test_legacy_cookie_csrf_still_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    # cookie-аутентификация: POST без CSRF-заголовка → 403 (правило не ослаблено)
    client, _ = _make_client(monkeypatch)
    jwt = _issue_jwt({"telegram_user_id": _TG_ID, "internal_user_id": _INTERNAL_ID, "email": None})
    client.cookies.set("session", jwt, domain="testserver")

    async def _dummy(request):  # pragma: no cover - unreachable при 403
        return JSONResponse({"ok": True})

    mini = Starlette(routes=[Route("/mutate", _with_csrf(_dummy), methods=["POST"])])
    mini_client = TestClient(mini)
    # переносим cookie-сессию в мини-приложение (CSRF проверяется до хендлера);
    # domain-параметр при cookies.set не указываем — в текущем httpx он ломает матчинг
    mini_client.cookies.set("session", jwt)
    r = mini_client.post("/mutate")
    assert r.status_code == 403
    assert r.json()["error"] == "csrf_validation_failed"

    csrf = generate_csrf_token()
    mini_client.cookies.set("csrf_token", csrf)
    r2 = mini_client.post("/mutate", headers={"X-CSRF-Token": csrf})
    assert r2.status_code == 200


def test_bearer_token_skips_csrf_on_mutating_route(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bearer-заголовок — не CSRF-вектор: POST без csrf-куки/заголовка проходит
    for var in ("XUI_ENABLED", "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("JWT_SECRET", _JWT_SECRET)
    middleware._token_auth_fail_store.clear()

    async def _dummy(request):
        return JSONResponse({"ok": True, "tg": request.state.user["telegram_user_id"]})

    pool = _TokenAuthFakePool(
        token_row={"telegram_user_id": _TG_ID, "internal_user_id": _INTERNAL_ID},
        profile_row=_profile_row(),
    )
    mini = Starlette(routes=[Route("/mutate", _with_auth(_with_csrf(_dummy)), methods=["POST"])])
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
