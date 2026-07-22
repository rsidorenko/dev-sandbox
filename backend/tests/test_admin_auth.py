"""Тесты админ-аутентификации (без БД): JWT, login, обёртка auth, CSRF.

Покрывает security-критичные инварианты (V4): отдельная кука admin_session, короткий
независимый TTL, строгий claim adm (is True), отдельный admin CSRF, изоляция от user-auth.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.web_api.admin_auth import (
    _ADMIN_CSRF_COOKIE,
    _ADMIN_SESSION_COOKIE,
    _decode_admin_jwt,
    _issue_admin_jwt,
    _with_admin_auth,
    _with_admin_csrf,
    handle_admin_login,
    handle_admin_logout,
)


def _set_admin_env(monkeypatch, *, enabled=True, user="admin", password="s3cr3t-pass"):
    monkeypatch.setenv("WEB_ADMIN_ENABLE", "1" if enabled else "0")
    monkeypatch.setenv("WEB_ADMIN_USERNAME", user)
    monkeypatch.setenv("WEB_ADMIN_PASSWORD", password)
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("JWT_ADMIN_TTL_MINUTES", "60")
    monkeypatch.setenv("WEB_API_DEV_INSECURE_COOKIE", "1")  # TestClient не по https


@_with_admin_auth
async def _protected(request):
    return JSONResponse({"ok": True, "who": request.state.admin.get("sub")})


@_with_admin_auth
@_with_admin_csrf
async def _mutation(request):
    return JSONResponse({"ok": True})


def _build_app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/admin/login", handle_admin_login, methods=["POST"]),
            Route("/admin/logout", handle_admin_logout, methods=["POST"]),
            Route("/admin/protected", _protected, methods=["GET"]),
            Route("/admin/mutation", _mutation, methods=["POST"]),
        ]
    )
    app.state.pool = None  # check_jwt_not_revoked пропускается при pool is None
    return app


def _craft_token(claims: dict, secret: str = "test-jwt-secret") -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"{header}.{body}.{sig}"


# --- JWT core ---


def test_admin_jwt_roundtrip_adm_strict(monkeypatch):
    _set_admin_env(monkeypatch)
    token = _issue_admin_jwt("admin")
    claims = _decode_admin_jwt(token)
    assert claims is not None
    assert claims.get("adm") is True
    assert claims.get("sub") == "admin"
    assert "jti" in claims and "exp" in claims


def test_admin_jwt_tampered_signature_rejected(monkeypatch):
    _set_admin_env(monkeypatch)
    token = _issue_admin_jwt("admin")
    tampered = token[:-4] + "aaaa"
    assert _decode_admin_jwt(tampered) is None


def test_admin_jwt_expired_rejected(monkeypatch):
    _set_admin_env(monkeypatch)
    past_exp = (datetime.now(UTC) - timedelta(minutes=1)).timestamp()
    expired = _craft_token({"adm": True, "sub": "admin", "exp": past_exp, "jti": "x"})
    assert _decode_admin_jwt(expired) is None


def test_admin_jwt_without_adm_claim_rejected(monkeypatch):
    _set_admin_env(monkeypatch)
    # Валидная подпись, но без claim adm — не должен считаться админским.
    future_exp = (datetime.now(UTC) + timedelta(minutes=5)).timestamp()
    noadm = _craft_token({"sub": "admin", "exp": future_exp, "jti": "y"})
    claims = _decode_admin_jwt(noadm)
    assert claims is not None
    assert claims.get("adm") is not True  # wrapper отвергнет это


def test_admin_jwt_wrong_secret_rejected(monkeypatch):
    _set_admin_env(monkeypatch)
    token = _craft_token(
        {"adm": True, "sub": "a", "exp": (datetime.now(UTC) + timedelta(minutes=5)).timestamp()}, secret="other"
    )
    assert _decode_admin_jwt(token) is None


# --- login flow ---


def test_login_disabled_returns_503(monkeypatch):
    _set_admin_env(monkeypatch, enabled=False)
    client = TestClient(_build_app())
    r = client.post("/admin/login", json={"username": "admin", "password": "s3cr3t-pass"})
    assert r.status_code == 503


def test_login_not_configured_returns_503(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_ENABLE", "1")
    monkeypatch.setenv("JWT_SECRET", "x")
    # username/password не заданы
    monkeypatch.delenv("WEB_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("WEB_ADMIN_PASSWORD", raising=False)
    client = TestClient(_build_app())
    r = client.post("/admin/login", json={"username": "a", "password": "b"})
    assert r.status_code == 503


def test_login_wrong_credentials_401(monkeypatch):
    _set_admin_env(monkeypatch)
    client = TestClient(_build_app())
    r = client.post("/admin/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    # куки не ставятся
    assert _ADMIN_SESSION_COOKIE not in client.cookies


def test_login_success_sets_isolated_cookies(monkeypatch):
    _set_admin_env(monkeypatch)
    client = TestClient(_build_app())
    r = client.post("/admin/login", json={"username": "admin", "password": "s3cr3t-pass"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["username"] == "admin"
    assert body["csrf_token"]
    # Изоляция: админская кука, а не пользовательская session
    assert _ADMIN_SESSION_COOKIE in client.cookies
    assert _ADMIN_CSRF_COOKIE in client.cookies
    assert "session" not in client.cookies  # user-кука не ставится


# --- protected route + auth wrapper ---


def test_protected_requires_admin_cookie(monkeypatch):
    _set_admin_env(monkeypatch)
    client = TestClient(_build_app())
    assert client.get("/admin/protected").status_code == 401


def test_protected_rejects_non_admin_token(monkeypatch):
    _set_admin_env(monkeypatch)
    client = TestClient(_build_app())
    future_exp = (datetime.now(UTC) + timedelta(minutes=5)).timestamp()
    noadm_token = _craft_token({"sub": "admin", "exp": future_exp, "jti": "z"})  # без adm
    client.cookies.set(_ADMIN_SESSION_COOKIE, noadm_token)
    assert client.get("/admin/protected").status_code == 401


def test_protected_accepts_after_login(monkeypatch):
    _set_admin_env(monkeypatch)
    client = TestClient(_build_app())
    client.post("/admin/login", json={"username": "admin", "password": "s3cr3t-pass"})
    r = client.get("/admin/protected")
    assert r.status_code == 200
    assert r.json()["who"] == "admin"


def test_logout_clears_admin_session(monkeypatch):
    _set_admin_env(monkeypatch)
    client = TestClient(_build_app())
    client.post("/admin/login", json={"username": "admin", "password": "s3cr3t-pass"})
    assert client.get("/admin/protected").status_code == 200
    client.post("/admin/logout")
    assert client.get("/admin/protected").status_code == 401


# --- CSRF on mutations ---


def test_mutation_blocked_without_csrf_header(monkeypatch):
    _set_admin_env(monkeypatch)
    client = TestClient(_build_app())
    client.post("/admin/login", json={"username": "admin", "password": "s3cr3t-pass"})
    # POST без x-csrf-token
    assert client.post("/admin/mutation").status_code == 403


def test_mutation_allowed_with_csrf_header(monkeypatch):
    _set_admin_env(monkeypatch)
    client = TestClient(_build_app())
    r = client.post("/admin/login", json={"username": "admin", "password": "s3cr3t-pass"})
    csrf = r.json()["csrf_token"]
    resp = client.post("/admin/mutation", headers={"x-csrf-token": csrf})
    assert resp.status_code == 200
