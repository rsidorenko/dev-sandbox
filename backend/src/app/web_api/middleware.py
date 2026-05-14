"""JWT middleware for web API — validates session cookie on protected endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import base64
from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

_LOGGER = logging.getLogger(__name__)


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


def require_auth(request: Request) -> dict[str, Any] | JSONResponse:
    """Extract and validate JWT from session cookie. Returns claims or error response."""
    token = request.cookies.get("session")
    if not token:
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    claims = _decode_jwt(token)
    if claims is None:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return claims
