"""Web API payment endpoints — create YooKassa payment session, check status, pay from balance."""

from __future__ import annotations

import logging
import os
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.application.balance_payment_service import (
    BalancePaymentResult,
    BalancePaymentStatus,
    add_device_from_balance,
    pay_plan_from_balance,
)
from app.application.funnel_events import record_interaction
from app.application.plan_pricing import (
    charge_metadata_fields,
    get_plan_pricing,
    resolve_effective_charge,
)
from app.domain.devices import DEFAULT_DEVICE_LIMIT, extra_device_cost
from app.domain.plans import calculate_total_price
from app.web_api.helpers import safe_json_error, truthy
from app.web_api.middleware import require_auth

_LOGGER = logging.getLogger(__name__)


ENV_YOOKASSA_ENABLED = "YOOKASSA_ENABLED"
ENV_CRYPTOBOT_ENABLED = "CRYPTOBOT_ENABLED"
ENV_RETURN_URL = "NEXT_PUBLIC_SITE_URL"


_VALID_PAYMENT_METHODS = ("card", "crypto")


def _parse_payment_method(data: dict[str, Any]) -> str:
    """Resolve the requested payment method from the request body (default ``card``)."""
    method = str(data.get("payment_method", "card")).strip().lower()
    return method if method in _VALID_PAYMENT_METHODS else "card"


def _payment_unavailable_response(
    plan_id: str, device_count: int, total_rubles: int, total_kopecks: int
) -> JSONResponse:
    """The 'method disabled' response — same shape the YooKassa-only gate returned."""
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
        }
    )


async def _cryptobot_create(
    *,
    amount_rubles: int,
    description: str,
    context: dict[str, object],
    return_url: str,
) -> tuple[str, str] | None:
    """Create a CryptoBot fiat-RUB invoice for *amount_rubles*, carrying *context* in the
    invoice payload. Returns ``(invoice_id, pay_url)`` or ``None`` when the client is not
    configured or creation failed (caller maps None → 502/503).
    """
    import os

    from app.cryptobot.client import CryptoBotClient
    from app.cryptobot.payload import encode_payment_context

    client = CryptoBotClient.from_env()
    if client is None:
        _LOGGER.error("cryptobot client not configured")
        return None
    ttl = int(os.environ.get("CRYPTOBOT_INVOICE_TTL_SECONDS", "3600"))
    try:
        result = await client.create_invoice(
            amount_rubles=amount_rubles,
            payload_str=encode_payment_context(context),
            description=description,
            paid_btn_url=return_url,
            expires_in=ttl,
        )
    except Exception:
        _LOGGER.exception("cryptobot create_invoice failed")
        return None
    return result.invoice_id, result.pay_url


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

    # Эффективная цена тарифа: админ-оверрайд (plan_price_overrides) поверх
    # код-дефолтов; custom:N всегда парсится кодом. pool может быть None
    # (in-memory тесты) — тогда провайдер отдаёт код-дефолты.
    pool = request.app.state.pool
    pricing = get_plan_pricing(pool)
    plan = await pricing.get_plan(plan_id)
    if plan is None:
        return safe_json_error(400, "invalid_plan_id")

    total_rubles = calculate_total_price(plan, device_count)
    total_kopecks = total_rubles * 100

    payment_method = _parse_payment_method(data)

    # Method availability (checked before any DB work, like the prior YooKassa-only gate).
    method_enabled = truthy(
        os.environ.get(ENV_CRYPTOBOT_ENABLED if payment_method == "crypto" else ENV_YOOKASSA_ENABLED)
    )
    if not method_enabled:
        return _payment_unavailable_response(plan_id, device_count, total_rubles, total_kopecks)

    return_url = os.environ.get(ENV_RETURN_URL, "").strip()
    if not return_url:
        return_url = "https://bravada-connect.ru"
    # /success — статичная страница: токен живёт в памяти клиента, полная
    # перезагрузка после оплаты = разлогин; /dashboard потребовал бы повторный вход.
    return_url = f"{return_url.rstrip('/')}/success"

    # Resolve the real internal_user_id from the identity row. Web-only (email)
    # accounts carry a NON-POSITIVE telegram_user_id and an internal_user_id of
    # "web_<hash>" (NOT f"u{telegram_user_id}"); the webhook must apply the
    # subscription to that exact identity, so we carry it in the payment context.
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return safe_json_error(404, "identity_not_found")
    internal_user_id = identity["internal_user_id"]

    from app.application.promo_service import resolve_active_promo
    from app.persistence.postgres_promo import (
        PostgresPromoAppliedRepository,
        PostgresPromoCodeRepository,
        PostgresPromoRedemptionRepository,
    )

    active_promo = await resolve_active_promo(
        pool=pool,
        promo_applied_repo=PostgresPromoAppliedRepository(pool),
        promo_code_repo=PostgresPromoCodeRepository(pool),
        promo_redemption_repo=PostgresPromoRedemptionRepository(pool),
        snapshots=None,
        internal_user_id=internal_user_id,
        base_rubles=total_rubles,
        plan_id=plan_id,
    )
    # Best-of(промокод, глобальная скидка) на одной базе. promo_code попадает в
    # metadata ТОЛЬКО когда выиграл промокод — иначе вебхук не погасит код зря.
    global_discount = await pricing.get_global_discount()
    resolved = resolve_effective_charge(
        base_rubles=total_rubles,
        plan_id=plan_id,
        promo=active_promo.promo if active_promo is not None else None,
        global_discount=global_discount,
    )
    charge_rubles = resolved.charge_rubles
    charge_kopecks = resolved.charge_kopecks

    if payment_method == "crypto":
        # CryptoBot carries the FULL context in its payload string (no client-side
        # injection like YooKassa). R-08: tg-id наружу не отправляем — вебхук
        # исполняет по internal_user_id.
        context: dict[str, object] = {
            "plan_id": plan_id,
            "device_count": str(device_count),
            "internal_user_id": internal_user_id,
        }
        # base_amount_kopecks — фиксация цены на момент создания (вебхук сверяет
        # по ней при смене цены админом); discount_kopecks — скидка победителя
        # best-of (раньше сюда ошибочно писался итоговый discounted_kopecks).
        context.update(
            charge_metadata_fields(
                resolved,
                promo_code=active_promo.code if active_promo is not None else None,
                global_discount=global_discount,
            )
        )
        created = await _cryptobot_create(
            amount_rubles=charge_rubles,
            description=f"Bravada VPN - {plan_display_name_safe(plan_id)}",
            context=context,
            return_url=return_url,
        )
        if created is None:
            return safe_json_error(502, "payment_creation_failed")
        invoice_id, pay_url = created
        await record_interaction(
            request.app.state.pool,
            event_name="payment_created",
            surface="web",
            dedup_key=f"cryptobot:{invoice_id}",
            internal_user_id=internal_user_id,
            plan_id=plan_id,
            device_count=device_count,
            payment_method="crypto",
        )
        return JSONResponse(
            {
                "ok": True,
                "status": "pending",
                "payment_method": "crypto",
                "plan_id": plan_id,
                "plan_name": plan_display_name_safe(plan_id),
                "device_count": device_count,
                "amount_rubles": charge_rubles,
                "amount_kopecks": charge_kopecks,
                "payment_url": pay_url,
                "payment_id": invoice_id,
            }
        )

    from app.security.checkout_reference import create_signed_checkout_reference
    from app.yookassa.client import YooKassaClient

    client = YooKassaClient.from_env()
    if client is None:
        _LOGGER.error("yookassa client not configured")
        return safe_json_error(503, "payment_provider_not_configured")

    checkout_secret = (
        os.environ.get("CHECKOUT_REFERENCE_SECRET") or os.environ.get("TELEGRAM_CHECKOUT_REFERENCE_SECRET", "").strip()
    )
    # Always carry the real identity so the webhook credits the correct account.
    payment_metadata: dict[str, str] = {"internal_user_id": internal_user_id}
    payment_metadata.update(
        charge_metadata_fields(
            resolved,
            promo_code=active_promo.code if active_promo is not None else None,
            global_discount=global_discount,
        )
    )
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
            amount_rubles=charge_rubles,
            plan_id=plan_id,
            device_count=device_count,
            return_url=return_url,
            description=f"Bravada VPN — {plan_display_name_safe(plan_id)}",
            metadata=payment_metadata,
        )
    except Exception:
        _LOGGER.exception("yookassa create_payment failed")
        return safe_json_error(502, "payment_creation_failed")

    await record_interaction(
        request.app.state.pool,
        event_name="payment_created",
        surface="web",
        dedup_key=f"yookassa:{result.payment_id}",
        internal_user_id=internal_user_id,
        plan_id=plan_id,
        device_count=device_count,
        payment_method="card",
    )
    return JSONResponse(
        {
            "ok": True,
            "status": "pending",
            "payment_method": "card",
            "plan_id": plan_id,
            "plan_name": plan_display_name_safe(plan_id),
            "device_count": device_count,
            "amount_rubles": charge_rubles,
            "amount_kopecks": charge_kopecks,
            "payment_url": result.confirmation_url,
            "payment_id": result.payment_id,
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
    # R-14: web-only аккаунты имеют internal_user_id "web_<hash>", а НЕ
    # f"u{telegram_user_id}" (их tg-id — синтетический отрицательный).
    # Резолвим настоящий id из identity-строки, как основной /payment/create.
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return safe_json_error(404, "identity_not_found")
    internal_user_id = identity["internal_user_id"]
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
    # Только duration_days (оверрайдами не меняется) — провайдер для единообразия.
    plan = await get_plan_pricing(pool).get_plan(plan_id)
    if plan is None:
        return safe_json_error(400, "invalid_plan")
    cost_rubles = extra_device_cost(new_device_count, current, plan.duration_days)
    if cost_rubles <= 0:
        return safe_json_error(400, "device_count_not_higher")
    cost_kopecks = cost_rubles * 100

    # Применённый промокод: cost_rubles И expected_amount_kopecks скидываются синхронно,
    # т.к. add-device вебхук сверяет платёж именно по expected_amount_kopecks из metadata.
    from app.application.promo_service import resolve_active_promo
    from app.persistence.postgres_promo import (
        PostgresPromoAppliedRepository,
        PostgresPromoCodeRepository,
        PostgresPromoRedemptionRepository,
    )

    active_promo = await resolve_active_promo(
        pool=pool,
        promo_applied_repo=PostgresPromoAppliedRepository(pool),
        promo_code_repo=PostgresPromoCodeRepository(pool),
        promo_redemption_repo=PostgresPromoRedemptionRepository(pool),
        snapshots=None,
        internal_user_id=internal_user_id,
        base_rubles=cost_rubles,
        is_addon=True,
    )
    charge_rubles = active_promo.price.discounted_rubles if active_promo is not None else cost_rubles
    expected_amount_kopecks = active_promo.price.discounted_kopecks if active_promo is not None else cost_kopecks
    add_device_meta: dict[str, str] = {
        "kind": "add_device",
        "internal_user_id": internal_user_id,
        "new_device_count": str(new_device_count),
        "expected_amount_kopecks": str(expected_amount_kopecks),
    }
    if active_promo is not None:
        add_device_meta["discount_kopecks"] = str(active_promo.price.discount_kopecks)
        add_device_meta["promo_code"] = active_promo.code

    payment_method = _parse_payment_method(data)
    method_enabled = truthy(
        os.environ.get(ENV_CRYPTOBOT_ENABLED if payment_method == "crypto" else ENV_YOOKASSA_ENABLED)
    )
    if not method_enabled:
        return safe_json_error(503, "payment_unavailable")

    return_url = os.environ.get(ENV_RETURN_URL, "https://bravada-connect.ru").strip().rstrip("/")

    if payment_method == "crypto":
        # internal_user_id уже в add_device_meta; tg-id наружу не отправляем (R-08).
        context: dict[str, object] = {**add_device_meta}
        created = await _cryptobot_create(
            amount_rubles=charge_rubles,
            description=f"Bravada VPN - add devices ({current}->{new_device_count})",
            context=context,
            return_url=f"{return_url}/success",
        )
        if created is None:
            return safe_json_error(502, "payment_creation_failed")
        invoice_id, pay_url = created
        return JSONResponse(
            {
                "ok": True,
                "status": "pending",
                "payment_method": "crypto",
                "device_count": new_device_count,
                "amount_rubles": charge_rubles,
                "amount_kopecks": expected_amount_kopecks,
                "payment_url": pay_url,
                "payment_id": invoice_id,
            }
        )

    from app.yookassa.client import YooKassaClient

    client = YooKassaClient.from_env()
    if client is None:
        _LOGGER.error("yookassa client not configured (add-device)")
        return safe_json_error(503, "payment_provider_not_configured")
    try:
        result = await client.create_payment(
            amount_rubles=charge_rubles,
            plan_id=plan_id,
            device_count=new_device_count,
            return_url=f"{return_url}/success",
            description=f"Bravada VPN — добавление устройств ({current}→{new_device_count})",
            metadata=add_device_meta,
        )
    except Exception:
        _LOGGER.exception("yookassa create_payment failed (add-device)")
        return safe_json_error(502, "payment_creation_failed")

    return JSONResponse(
        {
            "ok": True,
            "status": "pending",
            "payment_method": "card",
            "device_count": new_device_count,
            "amount_rubles": charge_rubles,
            "amount_kopecks": expected_amount_kopecks,
            "payment_url": result.confirmation_url,
            "payment_id": result.payment_id,
        }
    )


async def handle_create_balance_payment(request: Request) -> JSONResponse:
    """Оплата балансом: атомарное списание и сохраняемое задание выдачи доступа."""
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
    plan_id = str(data.get("plan_id", "")).strip()
    try:
        device_count = int(data.get("device_count", DEFAULT_DEVICE_LIMIT))
    except (ValueError, TypeError):
        return safe_json_error(400, "invalid_device_count")

    pool = request.app.state.pool
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return safe_json_error(404, "identity_not_found")

    result = await pay_plan_from_balance(
        pool=pool,
        composition=None,
        internal_user_id=identity["internal_user_id"],
        plan_id=plan_id,
        device_count=device_count,
        correlation_id=(request.headers.get("X-Idempotency-Key", "") or "").strip() or None,
        description_prefix="web balance payment",
        vless_provider=getattr(request.app.state, "vless_provider", None),
        notifier=getattr(request.app.state, "activation_notifier", None),
    )
    return _balance_result_to_response(result)


async def handle_create_add_device_balance_payment(request: Request) -> JSONResponse:
    """Покупка устройств без продления: списание и задание обновления лимита."""
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

    pool = request.app.state.pool
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return safe_json_error(404, "identity_not_found")

    result = await add_device_from_balance(
        pool=pool,
        composition=None,
        internal_user_id=identity["internal_user_id"],
        new_device_count=new_device_count,
        correlation_id=(request.headers.get("X-Idempotency-Key", "") or "").strip() or None,
        description_prefix="web add device",
        vless_provider=getattr(request.app.state, "vless_provider", None),
    )
    return _balance_result_to_response(result)


_BALANCE_ERROR_CODES: dict[BalancePaymentStatus, tuple[int, str]] = {
    BalancePaymentStatus.INSUFFICIENT: (402, "insufficient_balance"),
    BalancePaymentStatus.NO_ACTIVE: (409, "no_active_subscription"),
    BalancePaymentStatus.NOT_HIGHER: (400, "device_count_not_higher"),
    BalancePaymentStatus.IN_PROGRESS: (409, "payment_in_progress"),
    BalancePaymentStatus.INVALID_PLAN: (400, "invalid_plan_id"),
    BalancePaymentStatus.INVALID_DEVICE_COUNT: (400, "invalid_device_count"),
    BalancePaymentStatus.VLESS_FAILED: (503, "vless_creation_failed"),
    BalancePaymentStatus.INTERNAL_ERROR: (500, "balance_payment_failed"),
}


def _balance_result_to_response(result: BalancePaymentResult) -> JSONResponse:
    """Map the shared service result to the web JSON response."""
    if result.status in (BalancePaymentStatus.COMPLETED, BalancePaymentStatus.PROVISIONING):
        body: dict[str, Any] = {
            "ok": True,
            "status": result.status.value,
            "device_count": result.device_count,
            "amount_rubles": result.amount_kopecks // 100,
            "amount_kopecks": result.amount_kopecks,
            "balance_rubles_remaining": round(result.balance_after_kopecks / 100, 2),
        }
        if result.plan_id:
            body["plan_id"] = result.plan_id
            body["plan_name"] = plan_display_name_safe(result.plan_id)
        if result.active_until_utc:
            body["active_until"] = result.active_until_utc.date().isoformat()
        if result.status is BalancePaymentStatus.PROVISIONING:
            body["message"] = "Оплата принята. Доступ настраивается автоматически. Повторно платить не нужно."
        return JSONResponse(body, status_code=202 if result.status is BalancePaymentStatus.PROVISIONING else 200)
    if result.status is BalancePaymentStatus.ALREADY_COMPLETED:
        return JSONResponse(
            {
                "ok": True,
                "status": "already_completed",
                "device_count": result.device_count,
                "amount_rubles": 0,
                "amount_kopecks": 0,
                "balance_rubles_remaining": round(result.balance_after_kopecks / 100, 2),
            }
        )
    code, error = _BALANCE_ERROR_CODES[result.status]
    return safe_json_error(code, error)
