"""Админ-эндпоинты вкладки «Пользователи»: список (пагинация + поиск) и карточка."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.status_view import remaining_days
from app.web_api.admin_auth import _with_admin_auth
from app.web_api.helpers import safe_json_error

_LOGGER = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

# Полные SQL-строки как неявная конкатенация литералов (без '+' и интерполяции) —
# как в postgres_referral; в fetch передаётся по имени, поэтому S608 не срабатывает.

_LIST_SEARCH = (
    "SELECT i.internal_user_id, i.telegram_user_id, COALESCE(i.trial_used, FALSE) AS trial_used, "
    "s.state_label, s.active_until_utc, s.plan_id, s.device_count, "
    "COALESCE(rb.balance_kopecks, 0) AS balance_kopecks, ue.email "
    "FROM user_identities i "
    "LEFT JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
    "LEFT JOIN referral_balances rb ON rb.internal_user_id = i.internal_user_id "
    "LEFT JOIN LATERAL ("
    "SELECT email FROM user_emails WHERE telegram_user_id = i.telegram_user_id "
    "ORDER BY verified_at DESC NULLS LAST LIMIT 1"
    ") ue ON TRUE "
    "WHERE (ue.email ILIKE '%' || $1 || '%' OR CAST(i.telegram_user_id AS TEXT) LIKE $1 || '%') "
    "ORDER BY i.telegram_user_id DESC LIMIT $2 OFFSET $3"
)

_LIST_NO_SEARCH = (
    "SELECT i.internal_user_id, i.telegram_user_id, COALESCE(i.trial_used, FALSE) AS trial_used, "
    "s.state_label, s.active_until_utc, s.plan_id, s.device_count, "
    "COALESCE(rb.balance_kopecks, 0) AS balance_kopecks, ue.email "
    "FROM user_identities i "
    "LEFT JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
    "LEFT JOIN referral_balances rb ON rb.internal_user_id = i.internal_user_id "
    "LEFT JOIN LATERAL ("
    "SELECT email FROM user_emails WHERE telegram_user_id = i.telegram_user_id "
    "ORDER BY verified_at DESC NULLS LAST LIMIT 1"
    ") ue ON TRUE "
    "ORDER BY i.telegram_user_id DESC LIMIT $1 OFFSET $2"
)

_COUNT_SEARCH = (
    "SELECT COUNT(*) FROM user_identities i "
    "LEFT JOIN LATERAL ("
    "SELECT email FROM user_emails WHERE telegram_user_id = i.telegram_user_id "
    "ORDER BY verified_at DESC NULLS LAST LIMIT 1"
    ") ue ON TRUE "
    "WHERE (ue.email ILIKE '%' || $1 || '%' OR CAST(i.telegram_user_id AS TEXT) LIKE $1 || '%')"
)

_GET_BY_ID = (
    "SELECT i.internal_user_id, i.telegram_user_id, COALESCE(i.trial_used, FALSE) AS trial_used, "
    "s.state_label, s.active_until_utc, s.plan_id, s.device_count, "
    "COALESCE(rb.balance_kopecks, 0) AS balance_kopecks, ue.email "
    "FROM user_identities i "
    "LEFT JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
    "LEFT JOIN referral_balances rb ON rb.internal_user_id = i.internal_user_id "
    "LEFT JOIN LATERAL ("
    "SELECT email FROM user_emails WHERE telegram_user_id = i.telegram_user_id "
    "ORDER BY verified_at DESC NULLS LAST LIMIT 1"
    ") ue ON TRUE "
    "WHERE i.internal_user_id = $1"
)


def _row_to_user(row: asyncpg.Record) -> dict:
    active_until = row["active_until_utc"]
    return {
        "internal_user_id": row["internal_user_id"],
        "telegram_user_id": row["telegram_user_id"],
        "email": row["email"],
        "trial_used": bool(row["trial_used"]),
        "subscription_state": row["state_label"],
        "is_active": bool(
            row["state_label"] == "active" and active_until is not None and active_until > datetime.now(UTC)
        ),
        "active_until": active_until.isoformat() if active_until else None,
        "remaining_days": remaining_days(active_until) if active_until else None,
        "plan_id": row["plan_id"],
        "device_count": row["device_count"],
        "balance_rubles": round((row["balance_kopecks"] or 0) / 100, 2),
    }


def _parse_limit_offset(request: Request) -> tuple[int, int]:
    try:
        limit = max(1, min(_MAX_LIMIT, int(request.query_params.get("limit", _DEFAULT_LIMIT))))
    except ValueError:
        limit = _DEFAULT_LIMIT
    try:
        offset = max(0, int(request.query_params.get("offset", "0")))
    except ValueError:
        offset = 0
    return limit, offset


@_with_admin_auth
async def handle_admin_list_users(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    limit, offset = _parse_limit_offset(request)
    search = (request.query_params.get("search") or "").strip()

    if search:
        rows = await pool.fetch(_LIST_SEARCH, search, limit, offset)
        total = await pool.fetchval(_COUNT_SEARCH, search)
    else:
        rows = await pool.fetch(_LIST_NO_SEARCH, limit, offset)
        total = await pool.fetchval("SELECT COUNT(*) FROM user_identities i")

    return JSONResponse(
        {
            "ok": True,
            "users": [_row_to_user(r) for r in rows],
            "limit": limit,
            "offset": offset,
            "total": int(total or 0),
        }
    )


@_with_admin_auth
async def handle_admin_get_user(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    internal_user_id = request.path_params["internal_user_id"]

    row = await pool.fetchrow(_GET_BY_ID, internal_user_id)
    if row is None:
        return safe_json_error(404, "user_not_found")

    redemptions = await pool.fetch(
        "SELECT code, base_rubles, discounted_rubles, discount_kopecks, payment_ref, created_at "
        "FROM promo_redemptions WHERE internal_user_id = $1 ORDER BY created_at DESC LIMIT 50",
        internal_user_id,
    )

    return JSONResponse(
        {
            "ok": True,
            "user": _row_to_user(row),
            "promo_redemptions": [
                {
                    "code": r["code"],
                    "base_rubles": r["base_rubles"],
                    "discounted_rubles": r["discounted_rubles"],
                    "discount_kopecks": r["discount_kopecks"],
                    "payment_ref": r["payment_ref"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in redemptions
            ],
        }
    )
