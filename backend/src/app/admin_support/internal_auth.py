"""Shared-secret authentication for internal admin HTTP endpoints."""

from __future__ import annotations

import hmac
import os

from starlette.requests import Request
from starlette.responses import JSONResponse

_ENV_ADMIN_SECRET = "ADM_INTERNAL_SECRET"


def verify_internal_admin_secret(request: Request) -> JSONResponse | None:
    """Validate admin secret from header. Returns error response or None if OK.

    If ADM_INTERNAL_SECRET is not configured, returns None (backward compatible —
    security relies on network isolation as before).
    """
    secret = os.environ.get(_ENV_ADMIN_SECRET, "").strip()
    if not secret:
        return None
    header_val = request.headers.get("X-Admin-Secret", "").strip()
    if not header_val or not hmac.compare_digest(header_val, secret):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return None
