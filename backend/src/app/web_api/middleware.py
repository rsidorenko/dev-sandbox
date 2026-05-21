"""JWT middleware for web API — validates session cookie on protected endpoints."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

_LOGGER = logging.getLogger(__name__)

_CSRF_COOKIE_NAME = "csrf_token"
_CSRF_HEADER_NAME = "x-csrf-token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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


async def require_auth(request: Request) -> dict[str, Any] | JSONResponse:
    """Extract and validate JWT from session cookie. Returns claims or error response."""
    token = request.cookies.get("session")
    if not token:
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
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
    """Returns error response if CSRF check fails, None if OK."""
    if not validate_csrf(request):
        return JSONResponse({"ok": False, "error": "csrf_validation_failed"}, status_code=403)
    return None
