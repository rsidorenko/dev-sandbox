"""Auth middleware for web API.

Единственный способ аутентификации (link-auth, срезка email-логина 2026-09-20):
``Authorization: Bearer <subscription_token>`` — lookup по ``user_identities``.
Без entitlement-гейта: токен вечный, истёкшая подписка обязана видеть кабинет
и продлеваться (в отличие от ``/sub/``, который 403-ит истёкших). Куков нет —
CSRF для юзер-API не нужен вовсе.

Legacy email/JWT-логин удалён; ``check_jwt_not_revoked`` и ``generate_csrf_token``
остаются — их импортирует админка (``admin_auth.py``).
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

_LOGGER = logging.getLogger(__name__)

# Rate limit неудачных попыток token-auth (перебор/DB-нагрузка; успешные не считаются).
_TOKEN_AUTH_FAIL_MAX = 30
_TOKEN_AUTH_FAIL_WINDOW = 60
_TOKEN_AUTH_FAIL_MAX_ENTRIES = 10000
_token_auth_fail_store: dict[str, list[float]] = {}
_token_auth_fail_last_cleanup = 0.0


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
        # subscription-токены не содержат точек (secrets.token_urlsafe);
        # JWT-подобные строки отсюда сразу 401 без похода в БД.
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
    """Аутентификация: Bearer subscription-token. Возвращает claims-словарь
    либо error-ответ."""
    # Идемпотентность: обёртка _with_auth уже резолвила identity и положила его
    # в request.state — внутренний вызов из хендлера не ходит в БД второй раз.
    existing = getattr(request.state, "user", None)
    if existing is not None:
        return existing

    auth_header = request.headers.get("authorization", "").strip()
    if not auth_header:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    bearer = auth_header.removeprefix("Bearer ").strip()
    if not bearer:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return await _resolve_subscription_token(request, bearer)


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
