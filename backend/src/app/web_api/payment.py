"""Web API payment endpoints — create YooKassa payment session, check status."""

from __future__ import annotations

import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.plans import calculate_total_price, get_plan
from app.web_api.helpers import safe_json_error, truthy
from app.web_api.middleware import require_auth

_LOGGER = logging.getLogger(__name__)

ENV_YOOKASSA_ENABLED = "YOOKASSA_ENABLED"
ENV_RETURN_URL = "NEXT_PUBLIC_SITE_URL"


async def handle_create_payment(request: Request) -> JSONResponse:
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    telegram_user_id = auth_result.get("telegram_user_id")
    if telegram_user_id is None:
        return safe_json_error(403, "no_telegram_identity")

    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request")

    plan_id = data.get("plan_id", "").strip()
    device_count_raw = data.get("device_count", 5)
    try:
        device_count = int(device_count_raw)
    except (ValueError, TypeError):
        device_count = 5

    plan = get_plan(plan_id)
    if plan is None:
        return safe_json_error(400, "invalid_plan_id")

    total_rubles = calculate_total_price(plan, device_count)
    total_kopecks = total_rubles * 100

    yookassa_enabled = truthy(os.environ.get(ENV_YOOKASSA_ENABLED))

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

    return_url = os.environ.get(ENV_RETURN_URL, "").strip()
    if not return_url:
        return_url = "https://bravada-connect.ru"
    return_url = f"{return_url.rstrip('/')}/payment/success"

    from app.yookassa.client import YooKassaClient
    from app.security.checkout_reference import create_signed_checkout_reference

    client = YooKassaClient.from_env()
    if client is None:
        _LOGGER.error("yookassa client not configured")
        return safe_json_error(503, "payment_provider_not_configured")

    checkout_secret = os.environ.get("CHECKOUT_REFERENCE_SECRET") or os.environ.get(
        "TELEGRAM_CHECKOUT_REFERENCE_SECRET", ""
    ).strip()
    checkout_metadata: dict[str, str] = {}
    if checkout_secret:
        signed = create_signed_checkout_reference(
            telegram_user_id=telegram_user_id,
            internal_user_id=f"u{telegram_user_id}",
            secret=checkout_secret,
        )
        checkout_metadata["client_reference_id"] = signed.reference_id
        checkout_metadata["client_reference_proof"] = signed.reference_proof

    try:
        result = await client.create_payment(
            amount_rubles=total_rubles,
            plan_id=plan_id,
            device_count=device_count,
            telegram_user_id=telegram_user_id,
            return_url=return_url,
            description=f"Bravada VPN — {plan_display_name_safe(plan_id)}",
            metadata=checkout_metadata if checkout_metadata else None,
        )
    except Exception:
        _LOGGER.exception("yookassa create_payment failed")
        return safe_json_error(502, "payment_creation_failed")

    return JSONResponse({
        "ok": True,
        "status": "pending",
        "plan_id": plan_id,
        "plan_name": plan_display_name_safe(plan_id),
        "device_count": device_count,
        "amount_rubles": total_rubles,
        "amount_kopecks": total_kopecks,
        "payment_url": result.confirmation_url,
        "payment_id": result.payment_id,
    })


async def handle_get_payment_status(request: Request) -> JSONResponse:
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    payment_id = request.path_params.get("payment_id", "")
    if not payment_id:
        return safe_json_error(400, "missing_payment_id")

    return JSONResponse({
        "ok": True,
        "payment_id": payment_id,
        "status": "unknown",
    })


def plan_display_name_safe(plan_id: str) -> str:
    from app.domain.plans import plan_display_name
    return plan_display_name(plan_id)
