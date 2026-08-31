"""Публичный каталог тарифов — GET /api/v1/plans (без auth).

Единый runtime-источник цен для лендинга и страницы оплаты: эффективные цены
(админ-оверрайды через PlanPricingProvider) + глобальная скидка сайта. Ответ
кэшируется на клиенте/CDN на 30 секунд — столько же живёт in-process кэш цен
(PRICING_CACHE_TTL_SECONDS), поэтому фронт не увидит цену старее ~60с после
смены в админке.

Промокоды здесь НЕТ намеренно: скидка промокода — per-user состояние
(promo_applied), его расчёт живёт в /api/v1/user/promocode и apply-preview;
каталог отдаёт только site-wide скидку. Итоговая сумма — всегда ответ
сервера на /api/v1/payment/create (authoritative).
"""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.application.plan_pricing import get_plan_pricing
from app.domain.devices import DEFAULT_DEVICE_LIMIT, EXTRA_DEVICE_PRICE_RUBLES, MAX_DEVICE_COUNT
from app.domain.plans import CUSTOM_DAY_PRICE_RUBLES
from app.domain.pricing import global_discount_label

_LOGGER = logging.getLogger(__name__)

_CACHE_CONTROL = "public, max-age=30"


async def handle_list_plans(request: Request) -> Response:
    """Эффективные цены 7 тарифов + глобальная скидка + константы расчёта."""
    try:
        pricing = get_plan_pricing(request.app.state.pool)
        plans = await pricing.get_fixed_plans()
        global_discount = await pricing.get_global_discount()
        gd_descriptor = None
        if global_discount is not None and global_discount.is_active:
            gd_descriptor = {
                "discount_kind": global_discount.discount_kind.value,
                "discount_value": global_discount.discount_value,
                "label": global_discount_label(global_discount),
                "public_label": global_discount.public_label,
            }
        return JSONResponse(
            {
                "ok": True,
                "plans": [
                    {
                        "plan_id": p.plan_id.value,
                        "duration_days": p.duration_days,
                        "base_rubles": p.price_rubles,
                    }
                    for p in plans
                ],
                # Скидка сайта применяется только к 7 фиксированным тарифам — фронт
                # считает discounted сам (mirror effectivePrice.ts), custom:N — нет.
                "global_discount": gd_descriptor,
                "custom_day_price_rubles": CUSTOM_DAY_PRICE_RUBLES,
                "extra_device_price_rubles": EXTRA_DEVICE_PRICE_RUBLES,
                "devices": {"base": DEFAULT_DEVICE_LIMIT, "max": MAX_DEVICE_COUNT},
            },
            headers={"Cache-Control": _CACHE_CONTROL},
        )
    except Exception:
        _LOGGER.exception("plans_list_error")
        return JSONResponse({"ok": False, "error": "internal_error"}, status_code=500)
