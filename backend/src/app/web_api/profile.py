"""Web API profile endpoint — returns user subscription status and access info."""

from __future__ import annotations

import calendar
import contextlib
import logging
from datetime import UTC, datetime, timedelta

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.web_api.middleware import require_auth

_LOGGER = logging.getLogger(__name__)

_VALID_PLANS = {"1m", "3m", "6m", "plan_1m", "plan_3m", "plan_6m"}
_PLAN_DURATION = {"1m": 1, "3m": 3, "6m": 6, "plan_1m": 1, "plan_3m": 3, "plan_6m": 6}


def _safe_json_error(status_code: int, error: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error}, status_code=status_code)


async def handle_get_profile(request: Request) -> JSONResponse:
    try:
        return await _handle_get_profile_inner(request)
    except Exception as exc:
        _LOGGER.exception("profile_error")
        return _safe_json_error(500, "internal_error")


async def _handle_get_profile_inner(request: Request) -> JSONResponse:
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
        """SELECT state_label, active_until_utc, plan_id, device_count,
                  trial_started_at, trial_expires_at
           FROM subscription_snapshots WHERE internal_user_id = $1""",
        internal_user_id,
    )

    trial_used_row = await pool.fetchrow(
        "SELECT trial_used FROM user_identities WHERE internal_user_id = $1",
        internal_user_id,
    )
    trial_used = trial_used_row["trial_used"] if trial_used_row else False

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
            "trial_started_at": snapshot["trial_started_at"].isoformat() if snapshot["trial_started_at"] else None,
            "trial_expires_at": snapshot["trial_expires_at"].isoformat() if snapshot["trial_expires_at"] else None,
            "trial_available": not trial_used and not is_active,
        }
    elif not trial_used:
        subscription = {
            "state": "inactive",
            "active_until": None,
            "plan_id": None,
            "device_count": None,
            "trial_started_at": None,
            "trial_expires_at": None,
            "trial_available": True,
        }

    # Get keys info (only if subscription is active)
    keys_info = None
    if subscription and subscription["state"] == "active":
        issuance = await pool.fetchrow(
            """SELECT issuance_state
               FROM issuance_state WHERE internal_user_id = $1""",
            internal_user_id,
        )
        if issuance:
            keys_info = {
                "available": issuance["issuance_state"] == "issued",
                "status": issuance["issuance_state"],
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


async def handle_get_keys(request: Request) -> JSONResponse:
    try:
        auth_result = require_auth(request)
        if isinstance(auth_result, JSONResponse):
            return auth_result

        telegram_user_id = auth_result.get("telegram_user_id")
        if telegram_user_id is None:
            return _safe_json_error(403, "no_telegram_identity")

        pool: asyncpg.Pool = request.app.state.pool
        identity = await pool.fetchrow(
            "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
            telegram_user_id,
        )
        if identity is None:
            return _safe_json_error(404, "identity_not_found")

        provider = request.app.state.vless_provider
        from app.issuance.vless_provider import VlessProviderOutcome

        result = await provider.get_user_config(internal_user_id=identity["internal_user_id"])
        if result.outcome != VlessProviderOutcome.SUCCESS:
            result = await provider.create_user(internal_user_id=identity["internal_user_id"])
        if result.outcome != VlessProviderOutcome.SUCCESS or result.config is None:
            return JSONResponse({"ok": True, "keys": [], "subscription_url": None})

        keys = [
            {
                "label": s.server_label,
                "country": s.country_code,
                "flag": s.country_flag,
                "link": s.vless_link,
            }
            for s in result.config.servers
        ]
        return JSONResponse({
            "ok": True,
            "keys": keys,
            "subscription_url": result.config.subscription_url,
        })
    except Exception:
        _LOGGER.exception("keys_error")
        return _safe_json_error(500, "internal_error")


async def handle_reissue_keys(request: Request) -> JSONResponse:
    try:
        auth_result = require_auth(request)
        if isinstance(auth_result, JSONResponse):
            return auth_result

        telegram_user_id = auth_result.get("telegram_user_id")
        if telegram_user_id is None:
            return _safe_json_error(403, "no_telegram_identity")

        pool: asyncpg.Pool = request.app.state.pool
        identity = await pool.fetchrow(
            "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
            telegram_user_id,
        )
        if identity is None:
            return _safe_json_error(404, "identity_not_found")

        provider = request.app.state.vless_provider
        from app.issuance.vless_provider import VlessProviderOutcome

        await provider.revoke_user(internal_user_id=identity["internal_user_id"])
        result = await provider.create_user(internal_user_id=identity["internal_user_id"])
        if result.outcome != VlessProviderOutcome.SUCCESS or result.config is None:
            return _safe_json_error(500, "reissue_failed")

        keys = [
            {
                "label": s.server_label,
                "country": s.country_code,
                "flag": s.country_flag,
                "link": s.vless_link,
            }
            for s in result.config.servers
        ]
        return JSONResponse({
            "ok": True,
            "keys": keys,
            "subscription_url": result.config.subscription_url,
        })
    except Exception:
        _LOGGER.exception("reissue_error")
        return _safe_json_error(500, "internal_error")


async def _get_identity(request: Request) -> tuple[asyncpg.Pool, str] | JSONResponse:
    auth_result = require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result
    telegram_user_id = auth_result.get("telegram_user_id")
    if telegram_user_id is None:
        return _safe_json_error(403, "no_telegram_identity")
    pool: asyncpg.Pool = request.app.state.pool
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return _safe_json_error(404, "identity_not_found")
    return pool, identity["internal_user_id"]


def _plan_months(plan_id: str) -> int:
    return _PLAN_DURATION.get(plan_id, 1)


def _next_expiry(current_until: datetime | None, months: int) -> datetime:
    base = current_until if current_until and current_until > datetime.now(UTC) else datetime.now(UTC)
    new_month = base.month + months
    new_year = base.year + (new_month - 1) // 12
    new_month = ((new_month - 1) % 12) + 1
    max_day = calendar.monthrange(new_year, new_month)[1]
    return base.replace(year=new_year, month=new_month, day=min(base.day, max_day), hour=0, minute=0, second=0, microsecond=0)


async def handle_renew_subscription(request: Request) -> JSONResponse:
    try:
        result = await _get_identity(request)
        if isinstance(result, JSONResponse):
            return result
        pool, internal_user_id = result

        snapshot = await pool.fetchrow(
            """SELECT plan_id, active_until_utc, device_count, keys_deactivated_at, keys_deleted_at
               FROM subscription_snapshots WHERE internal_user_id = $1""",
            internal_user_id,
        )
        if snapshot is None:
            return _safe_json_error(404, "no_subscription")

        plan_id = snapshot["plan_id"] or "plan_1m"
        months = _plan_months(plan_id)
        new_until = _next_expiry(snapshot["active_until_utc"], months)

        # Handle key lifecycle on renewal
        provider = request.app.state.vless_provider
        if provider is not None:
            if snapshot["keys_deleted_at"] is not None:
                with contextlib.suppress(Exception):
                    await provider.create_user(internal_user_id=internal_user_id)
            elif snapshot["keys_deactivated_at"] is not None:
                with contextlib.suppress(Exception):
                    await provider.activate_user(internal_user_id=internal_user_id)

        await pool.execute(
            """UPDATE subscription_snapshots
               SET state_label = 'active', active_until_utc = $1,
                   keys_deactivated_at = NULL, keys_deleted_at = NULL, updated_at = NOW()
               WHERE internal_user_id = $2""",
            new_until,
            internal_user_id,
        )
        return JSONResponse({"ok": True, "active_until": new_until.isoformat()})
    except Exception:
        _LOGGER.exception("renew_error")
        return _safe_json_error(500, "internal_error")


async def handle_change_plan(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return _safe_json_error(400, "invalid_request")

    plan_id = data.get("plan_id", "").strip()
    if plan_id not in _VALID_PLANS:
        return _safe_json_error(400, "invalid_plan")

    try:
        result = await _get_identity(request)
        if isinstance(result, JSONResponse):
            return result
        pool, internal_user_id = result

        snapshot = await pool.fetchrow(
            "SELECT active_until_utc FROM subscription_snapshots WHERE internal_user_id = $1",
            internal_user_id,
        )
        if snapshot is None:
            return _safe_json_error(404, "no_subscription")

        months = _plan_months(plan_id)
        new_until = _next_expiry(snapshot["active_until_utc"], months)

        await pool.execute(
            """UPDATE subscription_snapshots
               SET plan_id = $1, active_until_utc = $2, updated_at = NOW()
               WHERE internal_user_id = $3""",
            plan_id,
            new_until,
            internal_user_id,
        )
        return JSONResponse({"ok": True, "plan_id": plan_id, "active_until": new_until.isoformat()})
    except Exception:
        _LOGGER.exception("change_plan_error")
        return _safe_json_error(500, "internal_error")


async def handle_change_devices(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return _safe_json_error(400, "invalid_request")

    try:
        device_count = int(data.get("device_count", 0))
    except (ValueError, TypeError):
        return _safe_json_error(400, "invalid_device_count")

    if device_count < 1 or device_count > 20:
        return _safe_json_error(400, "invalid_device_count")

    try:
        result = await _get_identity(request)
        if isinstance(result, JSONResponse):
            return result
        pool, internal_user_id = result

        await pool.execute(
            """UPDATE subscription_snapshots
               SET device_count = $1, updated_at = NOW()
               WHERE internal_user_id = $2""",
            device_count,
            internal_user_id,
        )
        return JSONResponse({"ok": True, "device_count": device_count})
    except Exception:
        _LOGGER.exception("change_devices_error")
        return _safe_json_error(500, "internal_error")


async def handle_cancel_subscription(request: Request) -> JSONResponse:
    try:
        result = await _get_identity(request)
        if isinstance(result, JSONResponse):
            return result
        pool, internal_user_id = result

        await pool.execute(
            """UPDATE subscription_snapshots
               SET state_label = 'cancelled', updated_at = NOW()
               WHERE internal_user_id = $1""",
            internal_user_id,
        )
        return JSONResponse({"ok": True, "state": "cancelled"})
    except Exception:
        _LOGGER.exception("cancel_error")
        return _safe_json_error(500, "internal_error")


async def handle_activate_trial(request: Request) -> JSONResponse:
    """Activate 3-day free trial: create VLESS keys, set trial period."""
    try:
        auth_result = require_auth(request)
        if isinstance(auth_result, JSONResponse):
            return auth_result

        telegram_user_id = auth_result.get("telegram_user_id")
        if telegram_user_id is None:
            return _safe_json_error(403, "no_telegram_identity")

        pool: asyncpg.Pool = request.app.state.pool
        identity = await pool.fetchrow(
            "SELECT internal_user_id, trial_used FROM user_identities WHERE telegram_user_id = $1",
            telegram_user_id,
        )
        if identity is None:
            return _safe_json_error(404, "identity_not_found")

        if identity["trial_used"]:
            return _safe_json_error(409, "trial_already_used")

        internal_user_id = identity["internal_user_id"]

        # Check no active subscription
        snap = await pool.fetchrow(
            "SELECT state_label, trial_started_at FROM subscription_snapshots WHERE internal_user_id = $1",
            internal_user_id,
        )
        if snap is not None and snap["state_label"] == "active":
            return _safe_json_error(409, "already_subscribed")
        if snap is not None and snap["trial_started_at"] is not None:
            return _safe_json_error(409, "trial_already_used")

        # Create VLESS user
        provider = request.app.state.vless_provider
        from app.issuance.vless_provider import VlessProviderOutcome

        vless_result = await provider.create_user(internal_user_id=internal_user_id)
        if vless_result.outcome != VlessProviderOutcome.SUCCESS or vless_result.config is None:
            return _safe_json_error(503, "vless_unavailable")

        # Set trial period
        from app.domain.trial import trial_expires_at

        now = datetime.now(UTC)
        expires = trial_expires_at(now)

        await pool.execute(
            """INSERT INTO subscription_snapshots (internal_user_id, state_label, active_until_utc, trial_started_at, trial_expires_at)
               VALUES ($1, 'active', $2, $3, $4)
               ON CONFLICT (internal_user_id) DO UPDATE
               SET state_label = 'active', active_until_utc = $2, trial_started_at = $3, trial_expires_at = $4, updated_at = now()""",
            internal_user_id,
            expires,
            now,
            expires,
        )

        # Mark trial used
        await pool.execute(
            "UPDATE user_identities SET trial_used = TRUE WHERE internal_user_id = $1",
            internal_user_id,
        )

        config = vless_result.config
        return JSONResponse({
            "ok": True,
            "trial": {
                "started_at": now.isoformat(),
                "expires_at": expires.isoformat(),
            },
            "keys": {
                "subscription_url": config.subscription_url,
                "servers": [
                    {
                        "label": s.server_label,
                        "country_code": s.country_code,
                        "country_flag": s.country_flag,
                        "vless_link": s.vless_link,
                    }
                    for s in config.servers
                ],
            },
        })
    except Exception:
        _LOGGER.exception("trial_activate_error")
        return _safe_json_error(500, "internal_error")
