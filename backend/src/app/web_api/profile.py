"""Web API profile endpoint — returns user subscription status and access info."""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.web_api.middleware import require_auth

_LOGGER = logging.getLogger(__name__)

_PLAN_DURATION_DAYS: dict[str, int] = {
    "1d": 1,
    "7d": 7,
    "14d": 14,
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "365d": 365,
}


def _safe_json_error(status_code: int, error: str, detail: str = "") -> JSONResponse:
    body: dict = {"ok": False, "error": error}
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status_code)


async def handle_get_profile(request: Request) -> JSONResponse:
    try:
        return await _handle_get_profile_inner(request)
    except Exception:
        _LOGGER.exception("profile_error")
        return _safe_json_error(500, "internal_error")


async def _handle_get_profile_inner(request: Request) -> JSONResponse:
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    telegram_user_id = auth_result.get("telegram_user_id")
    email = auth_result.get("email")
    if telegram_user_id is None:
        return _safe_json_error(403, "no_telegram_identity")

    pool: asyncpg.Pool = request.app.state.pool

    # Single JOIN query: identity + snapshot + trial_used + issuance_state + referral_code + balance
    row = await pool.fetchrow(
        """SELECT
               i.internal_user_id,
               COALESCE(i.trial_used, FALSE) AS trial_used,
               s.state_label, s.active_until_utc, s.plan_id, s.device_count,
               s.trial_started_at, s.trial_expires_at,
               iss.issuance_state,
               rc.referral_code,
               rb.balance_kopecks,
               COALESCE(l1.cnt, 0) AS referral_count
           FROM user_identities i
           LEFT JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id
           LEFT JOIN issuance_state iss ON iss.internal_user_id = i.internal_user_id
           LEFT JOIN referral_codes rc ON rc.internal_user_id = i.internal_user_id
           LEFT JOIN referral_balances rb ON rb.internal_user_id = i.internal_user_id
           LEFT JOIN LATERAL (
               SELECT COUNT(*) AS cnt FROM referral_relationships
               WHERE referred_user_id = i.internal_user_id AND level = 1
           ) l1 ON TRUE
           WHERE i.telegram_user_id = $1""",
        telegram_user_id,
    )

    if row is None or row["internal_user_id"] is None:
        return JSONResponse(
            {
                "ok": True,
                "user": {"telegram_user_id": telegram_user_id, "email": email},
                "subscription": None,
                "keys": None,
            }
        )

    trial_used = row["trial_used"]

    subscription = None
    if row["state_label"] is not None:
        is_active = (
            row["state_label"] == "active"
            and row["active_until_utc"] is not None
            and row["active_until_utc"] > datetime.now(UTC)
        )
        subscription = {
            "state": "active" if is_active else row["state_label"],
            "active_until": row["active_until_utc"].isoformat() if row["active_until_utc"] else None,
            "plan_id": row["plan_id"],
            "device_count": row["device_count"],
            "trial_started_at": row["trial_started_at"].isoformat() if row["trial_started_at"] else None,
            "trial_expires_at": row["trial_expires_at"].isoformat() if row["trial_expires_at"] else None,
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

    keys_info = None
    if subscription and subscription["state"] == "active" and row["issuance_state"] is not None:
        keys_info = {
            "available": row["issuance_state"] == "issued",
            "status": row["issuance_state"],
        }

    referral = None
    if row["referral_code"] is not None:
        from app.shared.site_url import get_site_base_url

        site_url = get_site_base_url()
        referral = {
            "code": row["referral_code"],
            "balance_rubles": round((row["balance_kopecks"] or 0) / 100, 2),
            "referrals_count": row["referral_count"] or 0,
            "web_referral_link": f"{site_url}/?ref={row['referral_code']}",
        }

    return JSONResponse(
        {
            "ok": True,
            "user": {
                "telegram_user_id": telegram_user_id,
                "email": email,
            },
            "subscription": subscription,
            "keys": keys_info,
            "referral": referral,
        }
    )


async def handle_get_keys(request: Request) -> JSONResponse:
    try:
        auth_result = await require_auth(request)
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
        return JSONResponse(
            {
                "ok": True,
                "keys": keys,
                "subscription_url": result.config.subscription_url,
            }
        )
    except Exception:
        _LOGGER.exception("keys_error")
        return _safe_json_error(500, "internal_error")


async def handle_reissue_keys(request: Request) -> JSONResponse:
    try:
        auth_result = await require_auth(request)
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

        internal_user_id = identity["internal_user_id"]
        provider = request.app.state.vless_provider
        from app.issuance.vless_provider import VlessProviderOutcome

        # Save old UUID for compensation if reissue fails
        old_uuid_row = await pool.fetchrow(
            "SELECT vless_uuid FROM user_identities WHERE internal_user_id = $1",
            internal_user_id,
        )
        old_uuid = old_uuid_row["vless_uuid"] if old_uuid_row else None

        # Step 1: Revoke FIRST (while old UUID still exists in DB)
        # so the provider can find and disable/delete old keys on panels.
        await provider.revoke_user(internal_user_id=internal_user_id)
        # Step 2: Clear stored UUID so create_user generates a fresh random key
        await pool.execute(
            "UPDATE user_identities SET vless_uuid = NULL WHERE internal_user_id = $1",
            internal_user_id,
        )
        # Step 3: Create new keys with a fresh UUID
        result = await provider.create_user(internal_user_id=internal_user_id)
        if result.outcome != VlessProviderOutcome.SUCCESS or result.config is None:
            # Compensation: restore old UUID so user isn't left keyless
            if old_uuid is not None:
                await pool.execute(
                    "UPDATE user_identities SET vless_uuid = $1 WHERE internal_user_id = $2 AND vless_uuid IS NULL",
                    old_uuid,
                    internal_user_id,
                )
                with contextlib.suppress(Exception):
                    await provider.create_user(internal_user_id=internal_user_id)
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
        return JSONResponse(
            {
                "ok": True,
                "keys": keys,
                "subscription_url": result.config.subscription_url,
            }
        )
    except Exception:
        _LOGGER.exception("reissue_error")
        return _safe_json_error(500, "internal_error")


async def _get_identity(request: Request) -> tuple[asyncpg.Pool, str] | JSONResponse:
    auth_result = await require_auth(request)
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


def _plan_duration_days(plan_id: str) -> int:
    return _PLAN_DURATION_DAYS.get(plan_id, 30)


def _next_expiry(current_until: datetime | None, days: int) -> datetime:
    from datetime import timedelta

    base = current_until if current_until and current_until > datetime.now(UTC) else datetime.now(UTC)
    return base + timedelta(days=days)


async def handle_renew_subscription(request: Request) -> JSONResponse:
    """Renew subscription — requires active payment confirmation.

    This endpoint is a placeholder until YooKassa integration is complete.
    Currently returns payment_unavailable to prevent free renewals via API.
    """
    try:
        result = await _get_identity(request)
        if isinstance(result, JSONResponse):
            return result
        return _safe_json_error(402, "payment_required", "Subscription renewal requires a confirmed payment")
    except Exception:
        _LOGGER.exception("renew_error")
        return _safe_json_error(500, "internal_error")


async def handle_change_plan(request: Request) -> JSONResponse:
    """Change subscription plan — requires payment for the new plan.

    This endpoint is a placeholder until YooKassa integration is complete.
    Currently returns payment_unavailable to prevent free plan changes via API.
    """
    try:
        result = await _get_identity(request)
        if isinstance(result, JSONResponse):
            return result
        return _safe_json_error(402, "payment_required", "Plan change requires a confirmed payment")
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

        # Sync device limit to 3x-ui panels
        provider = request.app.state.vless_provider
        await provider.activate_user(internal_user_id=internal_user_id, device_count=device_count)

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

        # Revoke VLESS keys on panel
        provider = request.app.state.vless_provider
        await provider.revoke_user(internal_user_id=internal_user_id)

        return JSONResponse({"ok": True, "state": "cancelled"})
    except Exception:
        _LOGGER.exception("cancel_error")
        return _safe_json_error(500, "internal_error")


async def handle_activate_trial(request: Request) -> JSONResponse:
    """Activate 3-day free trial: create VLESS keys, set trial period.

    Uses atomic ``UPDATE ... WHERE trial_used = FALSE`` to prevent
    double-trial under concurrent requests.
    """
    try:
        auth_result = await require_auth(request)
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

        internal_user_id = identity["internal_user_id"]

        # Atomic claim: check subscription + trial_used in a single query to prevent TOCTOU race.
        claim_row = await pool.fetchrow(
            """UPDATE user_identities SET trial_used = TRUE
               WHERE internal_user_id = $1
                 AND COALESCE(trial_used, FALSE) = FALSE
                 AND NOT EXISTS (
                   SELECT 1 FROM subscription_snapshots
                   WHERE internal_user_id = user_identities.internal_user_id
                     AND (state_label = 'active' OR trial_started_at IS NOT NULL)
                 )
               RETURNING internal_user_id""",
            internal_user_id,
        )
        if claim_row is None:
            # Determine specific reason for rejection
            snap = await pool.fetchrow(
                "SELECT state_label, trial_started_at FROM subscription_snapshots WHERE internal_user_id = $1",
                internal_user_id,
            )
            if snap is not None and snap["state_label"] == "active":
                return _safe_json_error(409, "already_subscribed")
            return _safe_json_error(409, "trial_already_used")

        # Create VLESS user
        provider = request.app.state.vless_provider
        from app.issuance.vless_provider import VlessProviderOutcome

        vless_result = await provider.create_user(internal_user_id=internal_user_id)
        if vless_result.outcome != VlessProviderOutcome.SUCCESS or vless_result.config is None:
            # Roll back trial claim
            await pool.execute(
                "UPDATE user_identities SET trial_used = FALSE WHERE internal_user_id = $1",
                internal_user_id,
            )
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

        config = vless_result.config
        return JSONResponse(
            {
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
            }
        )
    except Exception:
        _LOGGER.exception("trial_activate_error")
        return _safe_json_error(500, "internal_error")
