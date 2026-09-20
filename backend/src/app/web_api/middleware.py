"""Auth middleware for web API.

Два способа аутентификации (link-auth миграция, 2026-09-20):

1. **Bearer subscription-token** — ``Authorization: Bearer <subscription_token>``
   (без entitlement-гейта: истёкшая подписка обязана уметь продлиться).
2. **Legacy JWT** — кука ``session`` или Bearer-JWT (переходный период; удаляется
   в срезке email-логина).

Bearer-заголовок отличаем по форме: JWT — три dot-части с валидной подписью,
subscription-токен — dot-free urlsafe строка.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

_LOGGER = logging.getLogger(__name__)

_CSRF_COOKIE_NAME = "csrf_token"
_CSRF_HEADER_NAME = "x-csrf-token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Rate limit неудачных попыток token-auth (перебор/DB-нагрузка; успешные не считаются).
_TOKEN_AUTH_FAIL_MAX = 30
_TOKEN_AUTH_FAIL_WINDOW = 60
_TOKEN_AUTH_FAIL_MAX_ENTRIES = 10000
_token_auth_fail_store: dict[str, list[float]] = {}
_token_auth_fail_last_cleanup = 0.0


def _get_jwt_secret() -> str | None:
    return os.environ.get("JWT_SECRET", "").strip() or None


def _decode_jwt(token: str) -> dict[str, Any] | None:
    secret = _get_jwt_secret()
    if not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, body_b64, sig = parts
    sig_input = f"{header_b64}.{body_b64}".encode()
    expected_sig = hmac.new(secret.encode(), sig_input, hashlib.sha256).hexdigest()
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


def _token_auth_fail_limit_hit(client_ip: str) -> bool:
    """IP уже исчерпала лимит неудачных попыток? (проверка ДО похода в БД)"""
    now = time.monotonic()
    window = [t for t in _token_auth_fail_store.get(client_ip, []) if now - t < _TOKEN_AUTH_FAIL_WINDOW]
    _token_auth_fail_store[client_ip] = window
    return len(window) >= _TOKEN_AUTH_FAIL_MAX


def _record_token_auth_fail(client_ip: str) -> None:
    global _token_auth_fail_last_cleanup
    now = time.monotonic()
    if now - _token_auth_fail_last_cleanup > _TOKEN_AUTH_FAIL_WINDOW:
        expired = [k for k, v in _token_auth_fail_store.items() if not v or now - v[-1] > _TOKEN_AUTH_FAIL_WINDOW]
        for k in expired:
            del _token_auth_fail_store[k]
        if len(_token_auth_fail_store) > _TOKEN_AUTH_FAIL_MAX_ENTRIES:
            oldest = sorted(
                _token_auth_fail_store,
                key=lambda k: _token_auth_fail_store[k][-1] if _token_auth_fail_store[k] else 0,
            )
            for k in oldest[: len(_token_auth_fail_store) - _TOKEN_AUTH_FAIL_MAX_ENTRIES]:
                del _token_auth_fail_store[k]
        _token_auth_fail_last_cleanup = now
    _token_auth_fail_store.setdefault(client_ip, []).append(now)


async def _resolve_subscription_token(request: Request, token: str) -> dict[str, Any] | JSONResponse:
    """Bearer subscription-token → identity. Без entitlement-гейта: токен живёт
    вечно, а истёкшая подписка должна видеть кабинет и продлеваться."""
    client_ip = request.client.host if request.client else "unknown"
    if _token_auth_fail_limit_hit(client_ip):
        return JSONResponse({"ok": False, "error": "rate_limited"}, status_code=429)

    if not token or "." in token:
        # subscription-токены не содержат точек; JWT-подобные строки сюда не попадают
        _record_token_auth_fail(client_ip)
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    row = await pool.fetchrow(
        "SELECT telegram_user_id, internal_user_id FROM user_identities WHERE subscription_token = $1",
        token,
    )
    if row is None or row["telegram_user_id"] is None:
        _record_token_auth_fail(client_ip)
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return {
        "telegram_user_id": row["telegram_user_id"],
        "internal_user_id": row["internal_user_id"],
        "email": None,
        "auth_method": "subscription_token",
    }


async def require_auth(request: Request) -> dict[str, Any] | JSONResponse:
    """Аутентификация: Bearer (subscription-token или legacy JWT) либо кука session.
    Возвращает claims-словарь либо error-ответ."""
    # Идемпотентность: обёртка _with_auth уже резолвила identity и положила его
    # в request.state — внутренний вызов из хендлера не ходит в БД второй раз.
    existing = getattr(request.state, "user", None)
    if existing is not None:
        return existing

    auth_header = request.headers.get("authorization", "").strip()
    if auth_header:
        bearer = auth_header.removeprefix("Bearer ").strip()
        if not bearer:
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        # JWT-форма (подпись валидна) — legacy-путь; иначе — subscription-токен.
        claims = _decode_jwt(bearer)
        if claims is not None:
            revocation_err = await check_jwt_not_revoked(request, claims)
            if revocation_err is not None:
                return revocation_err
            return claims
        return await _resolve_subscription_token(request, bearer)

    token = request.cookies.get("session")
    if not token:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    claims = _decode_jwt(token)
    if claims is None:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    revocation_err = await check_jwt_not_revoked(request, claims)
    if revocation_err is not None:
        return revocation_err
    return claims


async def check_jwt_not_revoked(request: Request, claims: dict[str, Any]) -> JSONResponse | None:
    """Check if JWT jti is in revocation list. Returns error response if revoked, None if OK."""
    jti = claims.get("jti")
    if not jti:
        return None
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        return None
    row = await pool.fetchrow(
        "SELECT 1 FROM jwt_revocation_list WHERE jti = $1",
        jti,
    )
    if row is not None:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return None


def generate_csrf_token() -> str:
    """Generate a random CSRF token."""
    return secrets.token_urlsafe(32)


def validate_csrf(request: Request) -> bool:
    """Validate CSRF token: cookie must match header for non-safe methods."""
    if request.method in _SAFE_METHODS:
        return True
    cookie_token = request.cookies.get(_CSRF_COOKIE_NAME, "")
    header_token = request.headers.get(_CSRF_HEADER_NAME, "")
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)


def require_csrf(request: Request) -> JSONResponse | None:
    """Returns error response if CSRF check fails, None if OK.

    Bearer-заголовок — не CSRF-вектор: креденшл прикрепляется JS явно, браузер
    не отправляет его кросс-сайтно (custom header = CORS-preflight). Double-submit
    нужен только для cookie-аутентификации.
    """
    if request.headers.get("authorization", "").strip():
        return None
    if not validate_csrf(request):
        return JSONResponse({"ok": False, "error": "csrf_validation_failed"}, status_code=403)
    return None
