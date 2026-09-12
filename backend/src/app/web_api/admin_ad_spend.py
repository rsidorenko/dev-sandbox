"""Admin API ручного дневного расхода Telegram Ads."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.application.telegram_ads_attribution import parse_telegram_ads_campaign
from app.persistence.postgres_telegram_ads_spend import PostgresTelegramAdsSpendRepository
from app.web_api.admin_auth import _admin_actor, _with_admin_auth, _with_admin_csrf
from app.web_api.helpers import safe_json_error

_LOGGER = logging.getLogger(__name__)
_MIN_SPEND_DATE = date(2025, 1, 1)
_MAX_SPEND_KOPECKS = 10_000_000_000
_AMOUNT_RE = re.compile(r"^\d+(?:[.,]\d{1,2})?$")


def _normalize_campaign_key(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if value.startswith(("ads_", "tgads_")):
        return parse_telegram_ads_campaign(value)
    return parse_telegram_ads_campaign(f"ads_{value}")


def _parse_spend_date(raw: object) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        return None
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    if value < _MIN_SPEND_DATE or value > today_msk:
        return None
    return value


def _parse_amount_kopecks(raw: object) -> int | None:
    if not isinstance(raw, str) or not _AMOUNT_RE.fullmatch(raw.strip()):
        return None
    try:
        value = Decimal(raw.strip().replace(",", "."))
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0:
        return None
    kopecks = value * 100
    if kopecks != kopecks.to_integral_value():
        return None
    result = int(kopecks)
    return result if result <= _MAX_SPEND_KOPECKS else None


def _spend_json(row: dict) -> dict:
    kopecks = int(row["amount_kopecks"])
    return {
        "campaign_key": row["campaign_key"],
        "spend_date": row["spend_date"].isoformat(),
        "amount_kopecks": kopecks,
        "amount_rubles": f"{Decimal(kopecks) / 100:.2f}",
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] is not None else None,
        "changed": bool(row["changed"]),
    }


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_set_telegram_ads_spend(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request", "Expected JSON body")
    if not isinstance(data, dict):
        return safe_json_error(400, "invalid_request")

    campaign_key = _normalize_campaign_key(data.get("campaign_key"))
    if campaign_key is None:
        return safe_json_error(400, "invalid_campaign_key")
    spend_date = _parse_spend_date(data.get("spend_date"))
    if spend_date is None:
        return safe_json_error(400, "invalid_spend_date")
    amount_kopecks = _parse_amount_kopecks(data.get("amount_rubles"))
    if amount_kopecks is None:
        return safe_json_error(400, "invalid_amount")

    pool: asyncpg.Pool = request.app.state.pool
    try:
        row = await PostgresTelegramAdsSpendRepository(pool).set_daily_spend(
            campaign_key=campaign_key,
            spend_date=spend_date,
            amount_kopecks=amount_kopecks,
            actor=_admin_actor(request),
        )
        return JSONResponse({"ok": True, "spend": _spend_json(row)})
    except Exception:
        _LOGGER.exception("admin_telegram_ads_spend_set_error")
        return safe_json_error(500, "internal_error")
