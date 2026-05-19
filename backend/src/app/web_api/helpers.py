"""Shared helpers for web API handlers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from starlette.responses import JSONResponse


def truthy(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in ("1", "true", "yes")


def safe_json_error(status_code: int, error: str, detail: str = "") -> JSONResponse:
    body: dict[str, Any] = {"ok": False, "error": error}
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status_code)


def validate_email(email: str) -> bool:
    if not email or len(email) > 254:
        return False
    parts = email.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts
    return bool(local) and bool(domain) and "." in domain


def get_jwt_secret() -> str:
    import os

    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        raise ValueError("JWT_SECRET is not configured")
    return secret


def hash_code(code: str) -> str:
    secret = get_jwt_secret()
    return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()


def generate_code(length: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))
