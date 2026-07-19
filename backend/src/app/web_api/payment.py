"""Web API payment endpoints — create YooKassa payment session, check status."""

from __future__ import annotations

import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.devices import extra_device_cost
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

    pool = request.app.state.pool

    original_rubles = calculate_total_price(plan, device_count)
    total_rubles = original_rubles
    discount_percent = 0
    promo_code_raw = (data.get("promo_code") or "").strip()
    if promo_code_raw:
        from datetime import UTC, datetime

        from app.domain.promo import calculate_discounted_rubles, validate_promo
        from app.persistence.postgres_promo import PostgresPromoCodeRepository

        promo = await PostgresPromoCodeRepository(pool).get_by_code(promo_code_raw)
        err = validate_promo(promo, now=datetime.now(UTC))
        if err is not None:
            return safe_json_error(400, "promo_invalid", detail=err.value)
        discount_percent = promo.discount_percent
        total_rubles = calculate_discounted_rubles(original_rubles, discount_percent)

    total_kopecks = total_rubles * 100

    def _promo_fields() -> dict:
        if not promo_code_raw:
            return {}
        return {
            "promo_code": promo_code_raw,
            "discount_percent": discount_percent,
            "original_rubles": original_rubles,
            "discounted_rubles": total_rubles,
        }

    yookassa_enabled = truthy(os.environ.get(ENV_YOOKASSA_ENABLED))

    if not yookassa_enabled:
        return JSONResponse(
            {
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
                **_promo_fields(),
            }
        )

    return_url = os.environ.get(ENV_RETURN_URL, "").strip()
    if not return_url:
        return_url = "https://bravada-connect.ru"
    return_url = f"{return_url.rstrip('/')}/dashboard"

    from app.security.checkout_reference import create_signed_checkout_reference
    from app.yookassa.client import YooKassaClient

    client = YooKassaClient.from_env()
    if client is None:
        _LOGGER.error("yookassa client not configured")
        return safe_json_error(503, "payment_provider_not_configured")

    # Resolve the real internal_user_id from the identity row. Web-only (email)
    # accounts carry a NON-POSITIVE telegram_user_id and an internal_user_id of
    # "web_<hash>" (NOT f"u{telegram_user_id}"); the YooKassa webhook must apply
    # the subscription to that exact identity, so we carry it in the payment
    # metadata. Mirrors handle_get_keys / handle_activate_trial.
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return safe_json_error(404, "identity_not_found")
    internal_user_id = identity["internal_user_id"]

    # Spend the promo use only now (right before creating a real payment) — avoids
    # wasting a personal 1-use code on a request that never reaches the provider.
    if promo_code_raw:
        from app.persistence.postgres_promo import PostgresPromoCodeRepository

        if not await PostgresPromoCodeRepository(pool).consume(promo_code_raw):
            return safe_json_error(409, "promo_exhausted")

    checkout_secret = (
        os.environ.get("CHECKOUT_REFERENCE_SECRET") or os.environ.get("TELEGRAM_CHECKOUT_REFERENCE_SECRET", "").strip()
    )
    # Always carry the real identity so the webhook credits the correct account.
    payment_metadata: dict[str, str] = {"internal_user_id": internal_user_id}
    if discount_percent > 0:
        payment_metadata["promo_code"] = promo_code_raw
        payment_metadata["discount_percent"] = str(discount_percent)
        payment_metadata["expected_amount_kopecks"] = str(total_kopecks)
    # Checkout-reference correlation assumes a real Telegram identity —
    # validate_telegram_user_id rejects the non-positive ids used by web-only
    # accounts (which previously raised here, outside any try/except, and crashed
    # the request with a 500). The reference is also not consumed by the YooKassa
    # webhook path, so skip it for web-only users; they are correlated by
    # metadata.internal_user_id instead.
    if checkout_secret and isinstance(telegram_user_id, int) and telegram_user_id > 0:
        signed = create_signed_checkout_reference(
            telegram_user_id=telegram_user_id,
            internal_user_id=internal_user_id,
            secret=checkout_secret,
        )
        payment_metadata["client_reference_id"] = signed.reference_id
        payment_metadata["client_reference_proof"] = signed.reference_proof

    try:
        result = await client.create_payment(
            amount_rubles=total_rubles,
            plan_id=plan_id,
            device_count=device_count,
            telegram_user_id=telegram_user_id,
            return_url=return_url,
            description=f"Bravada VPN — {plan_display_name_safe(plan_id)}",
            metadata=payment_metadata,
        )
    except Exception:
        _LOGGER.exception("yookassa create_payment failed")
        return safe_json_error(502, "payment_creation_failed")

    return JSONResponse(
        {
            "ok": True,
            "status": "pending",
            "plan_id": plan_id,
            "plan_name": plan_display_name_safe(plan_id),
            "device_count": device_count,
            "amount_rubles": total_rubles,
            "amount_kopecks": total_kopecks,
            "payment_url": result.confirmation_url,
            "payment_id": result.payment_id,
            **_promo_fields(),
        }
    )


async def handle_get_payment_status(request: Request) -> JSONResponse:
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    payment_id = request.path_params.get("payment_id", "")
    if not payment_id:
        return safe_json_error(400, "missing_payment_id")

    from app.yookassa.client import YooKassaClient

    client = YooKassaClient.from_env()
    if client is None:
        return JSONResponse({"ok": True, "payment_id": payment_id, "status": "unknown"})

    try:
        info = await client.get_payment(payment_id)
    except Exception:
        _LOGGER.warning("yookassa get_payment failed id=%s", payment_id, exc_info=True)
        return JSONResponse({"ok": True, "payment_id": payment_id, "status": "unknown"})

    if info is None:
        return JSONResponse({"ok": True, "payment_id": payment_id, "status": "not_found"})

    return JSONResponse(
        {
            "ok": True,
            "payment_id": payment_id,
            "status": info.status,
            "amount": info.amount_value,
            "metadata": info.metadata,
        }
    )


def plan_display_name_safe(plan_id: str) -> str:
    from app.domain.plans import plan_display_name

    return plan_display_name(plan_id)


async def handle_create_add_device_payment(request: Request) -> JSONResponse:
    """Create a YooKassa payment to ADD devices to an existing active subscription.

    Unlike ``/payment/create`` (a full plan purchase + renewal), this charges only
    for the extra devices (delta x daily device price x plan duration) and does NOT
    extend the subscription. The payment carries ``kind=add_device`` so the YooKassa
    webhook's add-device path raises ``device_count`` without touching
    ``active_until`` — consistent with the bot's add-device flow.
    """
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
    try:
        new_device_count = int(data.get("device_count", 0))
    except (ValueError, TypeError):
        return safe_json_error(400, "invalid_device_count")
    if new_device_count < 1 or new_device_count > 20:
        return safe_json_error(400, "invalid_device_count")

    pool = request.app.state.pool
    internal_user_id = f"u{telegram_user_id}"
    snap = await pool.fetchrow(
        "SELECT device_count, plan_id, state_label FROM subscription_snapshots WHERE internal_user_id = $1",
        internal_user_id,
    )
    if snap is None or snap["state_label"] != "active":
        return safe_json_error(409, "no_active_subscription")
    current = snap["device_count"] or 5
    if new_device_count <= current:
        return safe_json_error(400, "device_count_not_higher")

    plan_id = snap["plan_id"] or "1m"
    plan = get_plan(plan_id)
    if plan is None:
        return safe_json_error(400, "invalid_plan")
    cost_rubles = extra_device_cost(new_device_count, current, plan.duration_days)
    if cost_rubles <= 0:
        return safe_json_error(400, "device_count_not_higher")
    cost_kopecks = cost_rubles * 100

    if not truthy(os.environ.get(ENV_YOOKASSA_ENABLED)):
        return safe_json_error(503, "payment_unavailable")

    return_url = os.environ.get(ENV_RETURN_URL, "https://bravada-connect.ru").strip().rstrip("/")
    from app.yookassa.client import YooKassaClient

    client = YooKassaClient.from_env()
    if client is None:
        _LOGGER.error("yookassa client not configured (add-device)")
        return safe_json_error(503, "payment_provider_not_configured")
    try:
        result = await client.create_payment(
            amount_rubles=cost_rubles,
            plan_id=plan_id,
            device_count=new_device_count,
            telegram_user_id=telegram_user_id,
            return_url=f"{return_url}/dashboard",
            description=f"Bravada VPN — добавление устройств ({current}→{new_device_count})",
            metadata={
                "kind": "add_device",
                "new_device_count": str(new_device_count),
                "expected_amount_kopecks": str(cost_kopecks),
            },
        )
    except Exception:
        _LOGGER.exception("yookassa create_payment failed (add-device)")
        return safe_json_error(502, "payment_creation_failed")

    return JSONResponse(
        {
            "ok": True,
            "status": "pending",
            "device_count": new_device_count,
            "amount_rubles": cost_rubles,
            "amount_kopecks": cost_kopecks,
            "payment_url": result.confirmation_url,
            "payment_id": result.payment_id,
        }
    )
