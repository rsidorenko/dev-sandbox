"""Web API payment endpoints — create payment session, check status (stub for now, YooKassa later)."""

from __future__ import annotations

import logging
import os
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.plans import calculate_total_price, get_plan
from app.web_api.middleware import require_auth

_LOGGER = logging.getLogger(__name__)

ENV_YOOKASSA_ENABLED = "YOOKASSA_ENABLED"


def _safe_json_error(status_code: int, error: str, detail: str = "") -> JSONResponse:
    body: dict[str, Any] = {"ok": False, "error": error}
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status_code)


def _truthy(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in ("1", "true", "yes")


async def handle_create_payment(request: Request) -> JSONResponse:
    auth_result = require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    telegram_user_id = auth_result.get("telegram_user_id")
    if telegram_user_id is None:
        return _safe_json_error(403, "no_telegram_identity")

    try:
        data = await request.json()
    except Exception:
        return _safe_json_error(400, "invalid_request")

    plan_id = data.get("plan_id", "").strip()
    device_count_raw = data.get("device_count", 5)
    try:
        device_count = int(device_count_raw)
    except (ValueError, TypeError):
        device_count = 5

    plan = get_plan(plan_id)
    if plan is None:
        return _safe_json_error(400, "invalid_plan_id")

    total_rubles = calculate_total_price(plan, device_count)
    total_kopecks = total_rubles * 100

    yookassa_enabled = _truthy(os.environ.get(ENV_YOOKASSA_ENABLED))

    if not yookassa_enabled:
        return JSONResponse({
            "ok": True,
            "status": "payment_unavailable",
            "plan_id": plan_id,
            "plan_name": plan_display_name_safe(plan_id),
            "device_count": device_count,
            "amount_rubles": total_rubles,
            "amount_kopecks": total_kopecks,
            "message": (
                "Для оформления подписки нажмите кнопку ниже. "
                "После подтверждения оплаты доступ к сервису будет активирован автоматически."
            ),
        })

    # When YooKassa is connected, create payment here and return payment_url
    # payment_id = str(uuid.uuid4())
    # ... YooKassa API call ...
    # return JSONResponse({"ok": True, "status": "pending", "payment_url": url, "payment_id": payment_id})

    return _safe_json_error(501, "not_implemented", "Payment provider integration pending")


async def handle_get_payment_status(request: Request) -> JSONResponse:
    auth_result = require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    payment_id = request.path_params.get("payment_id", "")
    if not payment_id:
        return _safe_json_error(400, "missing_payment_id")

    # Stub: always return unavailable until YooKassa is connected
    return JSONResponse({
        "ok": True,
        "payment_id": payment_id,
        "status": "unknown",
    })


def plan_display_name_safe(plan_id: str) -> str:
    from app.domain.plans import plan_display_name
    return plan_display_name(plan_id)
