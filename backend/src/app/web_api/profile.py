"""Web API profile endpoint — returns user subscription status and access info."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.web_api.middleware import require_auth

_LOGGER = logging.getLogger(__name__)


def _safe_json_error(status_code: int, error: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error}, status_code=status_code)


async def handle_get_profile(request: Request) -> JSONResponse:
    auth_result = require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    telegram_user_id = auth_result.get("telegram_user_id")
    email = auth_result.get("email")
    if telegram_user_id is None:
        return _safe_json_error(403, "no_telegram_identity")

    pool: asyncpg.Pool = request.app.state.pool

    # Get subscription snapshot
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return JSONResponse({
            "ok": True,
            "user": {"telegram_user_id": telegram_user_id, "email": email},
            "subscription": None,
            "keys": None,
        })

    internal_user_id = identity["internal_user_id"]

    snapshot = await pool.fetchrow(
        """SELECT state_label, active_until_utc, plan_id, device_count
           FROM subscription_snapshots WHERE internal_user_id = $1""",
        internal_user_id,
    )

    subscription = None
    if snapshot:
        is_active = (
            snapshot["state_label"] == "active"
            and snapshot["active_until_utc"] is not None
            and snapshot["active_until_utc"] > datetime.now(UTC)
        )
        subscription = {
            "state": "active" if is_active else snapshot["state_label"],
            "active_until": snapshot["active_until_utc"].isoformat() if snapshot["active_until_utc"] else None,
            "plan_id": snapshot["plan_id"],
            "device_count": snapshot["device_count"],
        }

    # Get keys info (only if subscription is active)
    keys_info = None
    if subscription and subscription["state"] == "active":
        issuance = await pool.fetchrow(
            """SELECT operational_state, redacted_reference
               FROM issuance_state WHERE internal_user_id = $1""",
            internal_user_id,
        )
        if issuance:
            keys_info = {
                "available": issuance["operational_state"] == "issued",
                "status": issuance["operational_state"],
            }

    # Get referral info
    referral = None
    ref_code_row = await pool.fetchrow(
        "SELECT referral_code FROM referral_codes WHERE internal_user_id = $1",
        internal_user_id,
    )
    if ref_code_row:
        balance_row = await pool.fetchrow(
            "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id = $1",
            internal_user_id,
        )
        ref_count = await pool.fetchval(
            "SELECT COUNT(*) FROM referral_relationships WHERE referred_user_id = $1 AND level = 1",
            internal_user_id,
        )
        referral = {
            "code": ref_code_row["referral_code"],
            "balance_rubles": (balance_row["balance_kopecks"] or 0) / 100 if balance_row else 0,
            "referrals_count": ref_count or 0,
        }

    return JSONResponse({
        "ok": True,
        "user": {
            "telegram_user_id": telegram_user_id,
            "email": email,
            "internal_user_id": internal_user_id,
        },
        "subscription": subscription,
        "keys": keys_info,
        "referral": referral,
    })
