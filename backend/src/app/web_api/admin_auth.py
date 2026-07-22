"""Админ-аутентификация веб-панели: вход по username+password, короткий admin-JWT
в отдельной куке admin_session (изолирован от пользовательской session).

Изоляция от пользовательского auth (требования адверсариальной проверки V4):
- require_admin_auth читает ТОЛЬКО куку admin_session (require_auth хардкодит session —
  его переиспользовать нельзя, иначе admin-эндпоинты были бы «Bearer-only» или изоляция терялась);
- admin-JWT имеет независимый короткий TTL (JWT_ADMIN_TTL_MINUTES, по умолч. 60 мин),
  НЕ зависящий от пользовательского JWT_TTL_HOURS (24ч);
- claim 'adm' проверяется строго (is True), а не на «истинность»;
- отдельная admin_csrf_token кука, чтобы параллельные сессии админа и юзера не затирали csrf_token;
- logout — отдельный обработчик (читает/отзывает именно admin_session);
- опциональный отдельный секрет JWT_ADMIN_SECRET (fallback на JWT_SECRET).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.web_api.helpers import safe_json_error, truthy
from app.web_api.middleware import check_jwt_not_revoked, generate_csrf_token

_LOGGER = logging.getLogger(__name__)

ENV_ADMIN_ENABLE = "WEB_ADMIN_ENABLE"
ENV_ADMIN_USERNAME = "WEB_ADMIN_USERNAME"
ENV_ADMIN_PASSWORD = "WEB_ADMIN_PASSWORD"
ENV_ADMIN_TTL_MINUTES = "JWT_ADMIN_TTL_MINUTES"
ENV_ADMIN_SECRET = "JWT_ADMIN_SECRET"
ENV_DEV_INSECURE_COOKIE = "WEB_API_DEV_INSECURE_COOKIE"

_DEFAULT_ADMIN_TTL_MINUTES = 60
_ADMIN_SESSION_COOKIE = "admin_session"
_ADMIN_CSRF_COOKIE = "admin_csrf_token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def admin_enabled() -> bool:
    return truthy(os.environ.get(ENV_ADMIN_ENABLE))


def _admin_secret() -> str:
    """Секрет для admin-JWT: отдельный при наличии JWT_ADMIN_SECRET, иначе общий JWT_SECRET."""
    s = os.environ.get(ENV_ADMIN_SECRET, "").strip()
    if s:
        return s
    return os.environ.get("JWT_SECRET", "").strip()


def _admin_ttl_minutes() -> int:
    raw = os.environ.get(ENV_ADMIN_TTL_MINUTES, "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return _DEFAULT_ADMIN_TTL_MINUTES


def _cookie_secure() -> bool:
    return not truthy(os.environ.get(ENV_DEV_INSECURE_COOKIE))


def _issue_admin_jwt(username: str) -> str:
    secret = _admin_secret()
    if not secret:
        raise ValueError("admin secret is not configured")
    ttl = _admin_ttl_minutes()
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    exp = datetime.now(UTC) + timedelta(minutes=ttl)
    claims: dict[str, Any] = {"adm": True, "sub": username, "exp": exp.timestamp(), "jti": str(uuid.uuid4())}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"{header}.{body}.{sig}"


def _decode_admin_jwt(token: str) -> dict[str, Any] | None:
    secret = _admin_secret()
    if not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, body_b64, sig = parts
    expected_sig = hmac.new(secret.encode(), f"{header_b64}.{body_b64}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        padding = 4 - len(body_b64) % 4
        if padding != 4:
            body_b64 += "=" * padding
        claims = json.loads(base64.urlsafe_b64decode(body_b64))
    except Exception:
        return None
    exp = claims.get("exp")
    if exp is not None and datetime.now(UTC).timestamp() > exp:
        return None
    return claims


async def require_admin_auth(request: Request) -> dict[str, Any] | JSONResponse:
    """Декодирует admin_session куку и проверяет claim adm. Возвращает claims или 401/503."""
    if not admin_enabled():
        return JSONResponse({"ok": False, "error": "admin_disabled"}, status_code=503)
    token = request.cookies.get(_ADMIN_SESSION_COOKIE)
    if not token:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    claims = _decode_admin_jwt(token)
    if claims is None or claims.get("adm") is not True:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    revocation_err = await check_jwt_not_revoked(request, claims)
    if revocation_err is not None:
        return revocation_err
    return claims


def _with_admin_auth(handler):
    """Обёртка: требует валидный admin-JWT, кладёт claims в request.state.admin."""

    async def _wrapped(request: Request) -> JSONResponse:
        try:
            result = await require_admin_auth(request)
            if isinstance(result, JSONResponse):
                return result
            request.state.admin = result
            return await handler(request)
        except Exception:
            _LOGGER.exception("admin_auth_error")
            return safe_json_error(500, "internal_error")

    return _wrapped


def require_admin_csrf(request: Request) -> JSONResponse | None:
    """CSRF по двойной передаче: admin_csrf_token кука == x-csrf-token заголовок (для мутаций)."""
    if request.method in _SAFE_METHODS:
        return None
    cookie_token = request.cookies.get(_ADMIN_CSRF_COOKIE, "")
    header_token = request.headers.get("x-csrf-token", "")
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        return JSONResponse({"ok": False, "error": "csrf_validation_failed"}, status_code=403)
    return None


def _with_admin_csrf(handler):
    async def _wrapped(request: Request) -> JSONResponse:
        csrf_err = require_admin_csrf(request)
        if csrf_err is not None:
            return csrf_err
        return await handler(request)

    return _wrapped


def _admin_actor(request: Request) -> str:
    return (getattr(request.state, "admin", None) or {}).get("sub", "admin")


async def handle_admin_login(request: Request) -> JSONResponse:
    if not admin_enabled():
        return safe_json_error(503, "admin_disabled")
    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request", "Expected JSON body")

    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    expected_user = os.environ.get(ENV_ADMIN_USERNAME, "").strip()
    expected_pass = os.environ.get(ENV_ADMIN_PASSWORD, "").strip()
    if not expected_user or not expected_pass:
        _LOGGER.error("WEB_ADMIN_USERNAME/PASSWORD not configured — admin login rejected")
        return safe_json_error(503, "admin_not_configured")

    user_ok = hmac.compare_digest(username, expected_user)
    pass_ok = hmac.compare_digest(password, expected_pass)
    if not (user_ok and pass_ok):
        return safe_json_error(401, "invalid_credentials")

    token = _issue_admin_jwt(username)
    csrf_token = generate_csrf_token()
    ttl_minutes = _admin_ttl_minutes()
    secure = _cookie_secure()

    response = JSONResponse({"ok": True, "csrf_token": csrf_token, "username": username})
    response.set_cookie(
        _ADMIN_SESSION_COOKIE,
        token,
        max_age=ttl_minutes * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        _ADMIN_CSRF_COOKIE,
        csrf_token,
        max_age=ttl_minutes * 60,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return response


async def handle_admin_logout(request: Request) -> JSONResponse:
    token = request.cookies.get(_ADMIN_SESSION_COOKIE)
    if token:
        claims = _decode_admin_jwt(token)
        if claims and claims.get("jti"):
            pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
            if pool is not None:
                await pool.execute(
                    "INSERT INTO jwt_revocation_list (jti) VALUES ($1) ON CONFLICT DO NOTHING",
                    claims["jti"],
                )
    secure = _cookie_secure()
    response = JSONResponse({"ok": True})
    response.delete_cookie(_ADMIN_SESSION_COOKIE, path="/", httponly=True, secure=secure, samesite="lax")
    response.delete_cookie(_ADMIN_CSRF_COOKIE, path="/", secure=secure, samesite="lax")
    return response
