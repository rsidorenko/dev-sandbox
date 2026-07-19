"""Promo code validation endpoint — preview the discount before payment (read-only, no consume)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.plans import calculate_total_price, get_plan
from app.domain.promo import calculate_discounted_rubles, validate_promo
from app.persistence.postgres_promo import PostgresPromoCodeRepository
from app.web_api.helpers import safe_json_error
from app.web_api.middleware import require_auth

_LOGGER = logging.getLogger(__name__)


async def handle_validate_promo(request: Request) -> JSONResponse:
    """POST /api/v1/promo/validate {code, plan_id, device_count}
    → {valid, discount_percent, original_rubles, discounted_rubles} or {valid:false, reason}.
    Read-only — does not consume the code (that happens at payment creation)."""
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request")

    code = (data.get("code") or "").strip()
    plan_id = (data.get("plan_id") or "").strip()
    try:
        device_count = int(data.get("device_count", 5))
    except (ValueError, TypeError):
        device_count = 5

    if not code:
        return safe_json_error(400, "missing_code")
    plan = get_plan(plan_id)
    if plan is None:
        return safe_json_error(400, "invalid_plan_id")

    pool = request.app.state.pool
    promo = await PostgresPromoCodeRepository(pool).get_by_code(code)
    err = validate_promo(promo, now=datetime.now(UTC))
    if err is not None:
        return JSONResponse({"ok": True, "valid": False, "reason": err.value})

    original = calculate_total_price(plan, device_count)
    discounted = calculate_discounted_rubles(original, promo.discount_percent)
    return JSONResponse(
        {
            "ok": True,
            "valid": True,
            "discount_percent": promo.discount_percent,
            "original_rubles": original,
            "discounted_rubles": discounted,
        }
    )
