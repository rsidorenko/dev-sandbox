"""Web API auth endpoints: send verification code, verify code, issue JWT."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.email.sender import send_verification_code

_LOGGER = logging.getLogger(__name__)

ENV_JWT_SECRET = "JWT_SECRET"
ENV_JWT_TTL_HOURS = "JWT_TTL_HOURS"
ENV_EMAIL_CODE_TTL_MINUTES = "EMAIL_CODE_TTL_MINUTES"

_DEFAULT_JWT_TTL_HOURS = 72
_DEFAULT_CODE_TTL_MINUTES = 10
_MAX_SEND_PER_EMAIL_PER_HOUR = 5
_CODE_LENGTH = 6


def _truthy(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in ("1", "true", "yes")


def _get_jwt_secret() -> str:
    secret = os.environ.get(ENV_JWT_SECRET, "").strip()
    if not secret:
        raise ValueError("JWT_SECRET is not configured")
    return secret


def _get_jwt_ttl_hours() -> int:
    raw = os.environ.get(ENV_JWT_TTL_HOURS, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return _DEFAULT_JWT_TTL_HOURS


def _get_code_ttl_minutes() -> int:
    raw = os.environ.get(ENV_EMAIL_CODE_TTL_MINUTES, "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return _DEFAULT_CODE_TTL_MINUTES


def _hash_code(code: str) -> str:
    secret = _get_jwt_secret()
    return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()


def _generate_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(_CODE_LENGTH))


def _validate_email(email: str) -> bool:
    if not email or len(email) > 254:
        return False
    parts = email.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    return True


def _issue_jwt(payload: dict[str, Any]) -> str:
    import base64

    secret = _get_jwt_secret()
    ttl = _get_jwt_ttl_hours()
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    exp = datetime.now(UTC) + timedelta(hours=ttl)
    claims = {**payload, "exp": exp.timestamp(), "jti": str(uuid.uuid4())}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    sig_input = f"{header}.{body}".encode()
    sig = hmac.new(secret.encode(), sig_input, hashlib.sha256).hexdigest()
    return f"{header}.{body}.{sig}"


def _safe_json_error(status_code: int, error: str, detail: str = "") -> JSONResponse:
    body: dict[str, Any] = {"ok": False, "error": error}
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status_code)


async def handle_send_code(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return _safe_json_error(400, "invalid_request", "Expected JSON body")

    email = data.get("email", "").strip().lower()
    if not _validate_email(email):
        return _safe_json_error(400, "invalid_email")

    pool: asyncpg.Pool = request.app.state.pool
    ttl_minutes = _get_code_ttl_minutes()
    expires_at = datetime.now(UTC) + timedelta(minutes=ttl_minutes)

    # Rate limit: max N codes per email per hour
    recent = await pool.fetchval(
        """SELECT COUNT(*) FROM email_verification_codes
           WHERE email = $1 AND created_at > NOW() - INTERVAL '1 hour'""",
        email,
    )
    if recent is not None and recent >= _MAX_SEND_PER_EMAIL_PER_HOUR:
        return _safe_json_error(429, "rate_limited", "Too many codes sent. Try later.")

    code = _generate_code()
    code_hash = _hash_code(code)

    # Remove old codes for this email
    await pool.execute(
        "DELETE FROM email_verification_codes WHERE email = $1 AND purpose = 'auth'",
        email,
    )

    await pool.execute(
        """INSERT INTO email_verification_codes (email, code_hash, purpose, expires_at)
           VALUES ($1, $2, 'auth', $3)""",
        email,
        code_hash,
        expires_at,
    )

    sent = await send_verification_code(email, code)
    if not sent:
        _LOGGER.warning("web_api.auth.send_code_failed email=***")

    return JSONResponse({"ok": True, "sent": sent, "ttl_minutes": ttl_minutes})


async def handle_verify_code(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return _safe_json_error(400, "invalid_request")

    email = data.get("email", "").strip().lower()
    code = data.get("code", "").strip()
    if not _validate_email(email) or not code:
        return _safe_json_error(400, "invalid_request")

    pool: asyncpg.Pool = request.app.state.pool
    now = datetime.now(UTC)

    row = await pool.fetchrow(
        """SELECT id, code_hash, attempts, max_attempts, expires_at
           FROM email_verification_codes
           WHERE email = $1 AND purpose = 'auth' AND used_at IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        email,
    )
    if row is None:
        return _safe_json_error(400, "invalid_code", "Code not found or expired")

    if row["expires_at"] < now:
        return _safe_json_error(400, "code_expired")

    if row["attempts"] >= row["max_attempts"]:
        return _safe_json_error(400, "too_many_attempts")

    await pool.execute(
        "UPDATE email_verification_codes SET attempts = attempts + 1 WHERE id = $1",
        row["id"],
    )

    expected_hash = _hash_code(code)
    if not hmac.compare_digest(row["code_hash"], expected_hash):
        return _safe_json_error(400, "invalid_code")

    # Delete used code
    await pool.execute(
        "DELETE FROM email_verification_codes WHERE id = $1",
        row["id"],
    )

    # Find or create user identity via verified email
    user_email_row = await pool.fetchrow(
        "SELECT telegram_user_id FROM user_emails WHERE email = $1 AND is_verified = TRUE",
        email,
    )

    telegram_user_id: int | None = None
    internal_user_id: str | None = None

    if user_email_row:
        telegram_user_id = user_email_row["telegram_user_id"]
    else:
        # New email — auto-create a web-only identity
        await pool.execute(
            """INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at)
               VALUES (0, $1, TRUE, $2)
               ON CONFLICT (telegram_user_id, email) DO UPDATE SET is_verified = TRUE, verified_at = $2""",
            email,
            now,
        )

    if telegram_user_id is not None:
        identity = await pool.fetchrow(
            "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
            telegram_user_id,
        )
        internal_user_id = identity["internal_user_id"] if identity else f"u{telegram_user_id}"
    else:
        internal_user_id = f"web_{hashlib.sha256(email.encode()).hexdigest()[:12]}"

    token = _issue_jwt({
        "telegram_user_id": telegram_user_id,
        "internal_user_id": internal_user_id,
        "email": email,
    })

    response = JSONResponse({
        "ok": True,
        "token": token,
        "user": {
            "telegram_user_id": telegram_user_id,
            "email": email,
        },
    })
    ttl = _get_jwt_ttl_hours()
    response.set_cookie(
        key="session",
        value=token,
        max_age=ttl * 3600,
        httponly=True,
        secure=not _truthy(os.environ.get("WEB_API_DEV_INSECURE_COOKIE")),
        samesite="lax",
        path="/",
    )
    return response


async def handle_logout(request: Request) -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(key="session", path="/")
    return response
