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
from app.persistence.postgres_referral import PostgresReferralCodeRepository
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


def _tx_label(transaction_type: str, amount_kopecks: int) -> str:
    """Clean, user-facing label derived from type + amount sign. Never exposes the
    internal `description`/`idempotency_key` (which historically leaked webhook data)."""
    if transaction_type == "referral_credit":
        return "Реферальное начисление"
    if transaction_type == "subscription_payment":
        return "Возврат" if amount_kopecks > 0 else "Оплата подписки"
    return "Операция"


_TX_LIMIT_DEFAULT = 20
_TX_LIMIT_MAX = 100


def _parse_tx_filters(query_params) -> tuple[int | None, str | None, int, int]:
    """(level, tx_type, limit, offset) from the query string with safe bounds."""
    raw_level = (query_params.get("level") or "").strip()
    level = int(raw_level) if raw_level in ("1", "2") else None
    raw_type = (query_params.get("type") or "").strip().lower()
    tx_type = raw_type if raw_type in ("credit", "debit") else None
    try:
        limit = max(1, min(_TX_LIMIT_MAX, int(query_params.get("limit", _TX_LIMIT_DEFAULT))))
    except (TypeError, ValueError):
        limit = _TX_LIMIT_DEFAULT
    try:
        offset = max(0, int(query_params.get("offset", "0")))
    except (TypeError, ValueError):
        offset = 0
    return level, tx_type, limit, offset


async def _fetch_transactions(
    pool: asyncpg.Pool,
    internal_user_id: str,
    *,
    level: int | None,
    tx_type: str | None,
    limit: int,
    offset: int,
) -> list[asyncpg.Record]:
    """Referral transactions for the cabinet, with the referral level derived via LEFT
    JOIN to referral_relationships (payer = related_user_id). Fetches ``limit + 1`` rows
    so the caller can signal ``has_more``. Returns clean fields only — never
    description/idempotency_key/internal ids."""
    where = ["t.internal_user_id = $1"]
    args: list = [internal_user_id]
    idx = 2
    if tx_type == "credit":
        where.append(f"t.transaction_type = ${idx}")
        args.append("referral_credit")
        idx += 1
    elif tx_type == "debit":
        where.append(f"t.transaction_type = ${idx}")
        args.append("subscription_payment")
        idx += 1
    if level in (1, 2):
        # Only credit rows carry a payer ref; debit rows have related_user_id = NULL
        # and thus rr.level = NULL, so they are excluded by a level filter (correct).
        where.append(f"rr.level = ${idx}")
        args.append(level)
        idx += 1
    args.extend([limit + 1, offset])
    sql = (
        "SELECT t.transaction_type, t.amount_kopecks, t.related_plan_id, t.created_at, "
        "rr.level AS ref_level "
        "FROM referral_transactions t "
        "LEFT JOIN referral_relationships rr "
        "  ON rr.referred_user_id = t.related_user_id AND rr.referrer_user_id = t.internal_user_id "
        "WHERE " + " AND ".join(where) + " "
        "ORDER BY t.created_at DESC "
        f"LIMIT ${idx} OFFSET ${idx + 1}"
    )
    return await pool.fetch(sql, *args)


def _tx_to_dict(r: asyncpg.Record) -> dict:
    amount = r["amount_kopecks"]
    return {
        "label": _tx_label(r["transaction_type"], amount),
        "type": r["transaction_type"],
        "amount_rubles": round(rubles_from_kopecks(amount), 2),
        "level": r["ref_level"],
        "plan_id": r["related_plan_id"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


async def handle_get_referral_transactions(request: Request) -> JSONResponse:
    """Extended, filtered referral-transaction history for the cabinet. Returns ONLY clean
    fields (label/type/amount_rubles/level/plan_id/created_at) — never the internal
    description/idempotency_key. Filters: ?level=1|2 &type=credit|debit &limit &offset."""
    try:
        auth_result = await require_auth(request)
        if isinstance(auth_result, JSONResponse):
            return auth_result
        internal_user_id = auth_result.get("internal_user_id")
        if not internal_user_id:
            return _safe_json_error(403, "no_identity")
        pool: asyncpg.Pool = request.app.state.pool
        level, tx_type, limit, offset = _parse_tx_filters(request.query_params)
        rows = await _fetch_transactions(
            pool, internal_user_id, level=level, tx_type=tx_type, limit=limit, offset=offset
        )
        has_more = len(rows) > limit
        return JSONResponse(
            {
                "ok": True,
                "transactions": [_tx_to_dict(r) for r in rows[:limit]],
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
            }
        )
    except Exception:
        _LOGGER.exception("referral_transactions_error")
        return _safe_json_error(500, "internal_error")


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

        site_url = get_site_base_url()
        bot_username = os.environ.get(ENV_BOT_USERNAME, "").strip()

        links: dict[str, str] = {"web_link": f"{site_url}/?ref={referral_code}"}
        if bot_username:
            links["telegram_link"] = f"https://t.me/{bot_username}?start=ref_{referral_code}"

        recent_rows = await _fetch_transactions(
            pool,
            internal_user_id,
            level=None,
            tx_type=None,
            limit=_RECENT_TX_LIMIT,
            offset=0,
        )
        recent = [_tx_to_dict(r) for r in recent_rows[:_RECENT_TX_LIMIT]]

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
