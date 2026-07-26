"""Web API referral dashboard endpoint — stats, conversion, recent transactions.

Returns everything the /referral page needs in one authenticated call:
referral code + links, current balance, L1/L2 invited & paid counts
(conversion via billing_events_ledger), total earned (sum of referral_credit),
and the recent transaction log.
"""

from __future__ import annotations

import logging
import os

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.referral import rubles_from_kopecks
from app.persistence.postgres_referral import (
    PostgresReferralCodeRepository,
    PostgresReferralTransactionRepository,
)
from app.shared.site_url import get_site_base_url
from app.web_api.middleware import require_auth

_LOGGER = logging.getLogger(__name__)

ENV_BOT_USERNAME = "BOT_USERNAME"
_RECENT_TX_LIMIT = 20


def _safe_json_error(status_code: int, error: str, detail: str = "") -> JSONResponse:
    body: dict = {"ok": False, "error": error}
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status_code)


async def _level_stats(pool: asyncpg.Pool, referrer: str, level: int) -> tuple[int, int]:
    """(invited, paid) for a given referral level of `referrer`.

    "paid" = the invited user has at least one accepted billing event
    (billing_events_ledger.status='accepted') — i.e. has ever paid.
    """
    row = await pool.fetchrow(
        """SELECT
               COUNT(*) AS invited,
               COUNT(*) FILTER (
                   WHERE EXISTS (
                       SELECT 1 FROM billing_events_ledger b
                       WHERE b.internal_user_id = rr.referred_user_id
                         AND b.status = 'accepted'
                   )
               ) AS paid
           FROM referral_relationships rr
           WHERE rr.referrer_user_id = $1 AND rr.level = $2""",
        referrer,
        level,
    )
    if row is None:
        return 0, 0
    return row["invited"] or 0, row["paid"] or 0


async def handle_get_referral_stats(request: Request) -> JSONResponse:
    try:
        auth_result = await require_auth(request)
        if isinstance(auth_result, JSONResponse):
            return auth_result
        # The JWT is the source of truth for who the caller is: resolve the
        # internal_user_id directly from the claim instead of re-looking-up
        # user_identities by telegram_user_id. Web-only users (synthetic negative
        # telegram_user_id, internal_user_id "web_…") otherwise risked a 404 here
        # when their identity row was out of sync, while /profile silently returned
        # nulls — so the referral dashboard "didn't work" for them.
        internal_user_id = auth_result.get("internal_user_id")
        if not internal_user_id:
            return _safe_json_error(403, "no_identity")

        pool: asyncpg.Pool = request.app.state.pool

        # Existing referral code + balance (if any), keyed on internal_user_id.
        referral_code = await pool.fetchval(
            "SELECT referral_code FROM referral_codes WHERE internal_user_id = $1",
            internal_user_id,
        )
        balance_kopecks = (
            await pool.fetchval(
                "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id = $1",
                internal_user_id,
            )
            or 0
        )

        # Guarantee a referral code exists so the dashboard always has a link to share.
        if not referral_code:
            referral_code = (await PostgresReferralCodeRepository(pool).get_or_create(internal_user_id)).referral_code

        l1_invited, l1_paid = await _level_stats(pool, internal_user_id, 1)
        l2_invited, l2_paid = await _level_stats(pool, internal_user_id, 2)

        total_earned_kopecks = (
            await pool.fetchval(
                """SELECT COALESCE(SUM(amount_kopecks), 0)::bigint FROM referral_transactions
                   WHERE internal_user_id = $1 AND transaction_type = 'referral_credit'""",
                internal_user_id,
            )
            or 0
        )

        records = await PostgresReferralTransactionRepository(pool).list_by_user(
            internal_user_id, limit=_RECENT_TX_LIMIT
        )

        site_url = get_site_base_url()
        bot_username = os.environ.get(ENV_BOT_USERNAME, "").strip()

        links: dict[str, str] = {"web_link": f"{site_url}/?ref={referral_code}"}
        if bot_username:
            links["telegram_link"] = f"https://t.me/{bot_username}?start=ref_{referral_code}"

        recent = [
            {
                "type": r.transaction_type,
                "amount_rubles": round(rubles_from_kopecks(r.amount_kopecks), 2),
                "plan_id": r.related_plan_id,
                "description": r.description,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]

        return JSONResponse(
            {
                "ok": True,
                "referral": {
                    "code": referral_code,
                    **links,
                    "balance_rubles": round(rubles_from_kopecks(balance_kopecks), 2),
                    "stats": {
                        "level1": {"invited": l1_invited, "paid": l1_paid},
                        "level2": {"invited": l2_invited, "paid": l2_paid},
                        "total_earned_rubles": round(rubles_from_kopecks(total_earned_kopecks), 2),
                    },
                    "recent_transactions": recent,
                },
            }
        )
    except Exception:
        _LOGGER.exception("referral_stats_error")
        return _safe_json_error(500, "internal_error")
