"""Web API email linking endpoint — used by Telegram bot to initiate email verification."""

from __future__ import annotations

import hmac
import logging
from datetime import UTC, datetime, timedelta

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.email.sender import send_verification_code
from app.web_api.helpers import generate_code, hash_code, validate_email

_LOGGER = logging.getLogger(__name__)

_CODE_TTL_MINUTES = 10
_MAX_SEND_PER_EMAIL_PER_HOUR = 5


async def handle_bot_send_code(request: Request) -> JSONResponse:
    """Bot calls this to send verification code to user's email."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)

    email = data.get("email", "").strip().lower()
    telegram_user_id = data.get("telegram_user_id")

    if not validate_email(email):
        return JSONResponse({"ok": False, "error": "invalid_email"}, status_code=400)
    if telegram_user_id is None:
        return JSONResponse({"ok": False, "error": "invalid_telegram_user_id"}, status_code=400)

    pool: asyncpg.Pool = request.app.state.pool

    # Check if email already linked to this user
    existing = await pool.fetchrow(
        "SELECT telegram_user_id FROM user_emails WHERE email = $1 AND is_verified = TRUE",
        email,
    )
    if existing and existing["telegram_user_id"] == telegram_user_id:
        return JSONResponse({"ok": False, "error": "email_already_linked"}, status_code=409)
    if existing and existing["telegram_user_id"] != telegram_user_id:
        return JSONResponse({"ok": False, "error": "email_belongs_to_other_account"}, status_code=409)

    # Rate limit
    recent = await pool.fetchval(
        """SELECT COUNT(*) FROM email_verification_codes
           WHERE email = $1 AND created_at > NOW() - INTERVAL '1 hour'""",
        email,
    )
    if recent is not None and recent >= _MAX_SEND_PER_EMAIL_PER_HOUR:
        return JSONResponse({"ok": False, "error": "rate_limited"}, status_code=429)

    code = generate_code()
    code_hash = hash_code(code)
    expires_at = datetime.now(UTC) + timedelta(minutes=_CODE_TTL_MINUTES)

    await pool.execute(
        """INSERT INTO email_verification_codes (email, code_hash, purpose, telegram_user_id, expires_at)
           VALUES ($1, $2, 'link_email', $3, $4)""",
        email,
        code_hash,
        telegram_user_id,
        expires_at,
    )

    sent = await send_verification_code(email, code)
    return JSONResponse({"ok": True, "sent": sent})


async def handle_bot_verify_code(request: Request) -> JSONResponse:
    """Bot calls this to verify code and link email to telegram account."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)

    email = data.get("email", "").strip().lower()
    code = data.get("code", "").strip()
    telegram_user_id = data.get("telegram_user_id")

    if not validate_email(email) or not code or telegram_user_id is None:
        return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)

    pool: asyncpg.Pool = request.app.state.pool
    now = datetime.now(UTC)

    row = await pool.fetchrow(
        """SELECT id, code_hash, attempts, max_attempts, expires_at, telegram_user_id
           FROM email_verification_codes
           WHERE email = $1 AND purpose = 'link_email' AND used_at IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        email,
    )
    if row is None:
        return JSONResponse({"ok": False, "error": "invalid_code"}, status_code=400)

    if row["expires_at"] < now:
        return JSONResponse({"ok": False, "error": "code_expired"}, status_code=400)
    if row["attempts"] >= row["max_attempts"]:
        return JSONResponse({"ok": False, "error": "too_many_attempts"}, status_code=400)
    if row["telegram_user_id"] != telegram_user_id:
        return JSONResponse({"ok": False, "error": "invalid_code"}, status_code=400)

    await pool.execute(
        "UPDATE email_verification_codes SET attempts = attempts + 1 WHERE id = $1",
        row["id"],
    )

    expected_hash = hash_code(code)
    if not hmac.compare_digest(row["code_hash"], expected_hash):
        return JSONResponse({"ok": False, "error": "invalid_code"}, status_code=400)

    # Mark used
    await pool.execute(
        "UPDATE email_verification_codes SET used_at = $1 WHERE id = $2",
        now,
        row["id"],
    )

    # Ensure user identity exists
    await pool.execute(
        "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        telegram_user_id,
        f"u{telegram_user_id}",
    )

    # Remove any existing verified email for this user (replace)
    # DELETE old rows instead of marking unverified — prevents stale
    # verified_at timestamps and garbage accumulation in user_emails.
    await pool.execute(
        "DELETE FROM user_emails WHERE telegram_user_id = $1",
        telegram_user_id,
    )

    # Insert or update email
    await pool.execute(
        """INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at)
           VALUES ($1, $2, TRUE, $3)
           ON CONFLICT (telegram_user_id, email) DO UPDATE SET is_verified = TRUE, verified_at = $3""",
        telegram_user_id,
        email,
        now,
    )

    # Merge web-only account if this email was previously registered on the website
    from app.persistence.account_merge import merge_web_account_if_needed
    await merge_web_account_if_needed(pool, telegram_user_id, email)

    return JSONResponse({"ok": True})
