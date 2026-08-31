"""Админ-эндпоинты вкладки «Тарифы»: цены 7 тарифов + глобальная скидка.

GET    /api/v1/admin/plans                    — снимок для UI (дефолты, эффективные
                                                 цены, оверрайды, состояние скидки)
PATCH  /api/v1/admin/plans/{plan_id}          — задать цену тарифа (1..100000 ₽)
DELETE /api/v1/admin/plans/{plan_id}          — сброс к код-дефолту
PUT    /api/v1/admin/plans/global-discount    — глобальная скидка (вкл/выкл, %/₽)

Каждая мутация: аудит в admin_pricing_actions (actor/old/new) + инвалидация
in-process кэша цен (invalidate_all_plan_pricing) — новые цены сразу в боте,
web-чекауте и /api/v1/plans. Деактивация скидки может приходить без kind/value —
тогда сохраняем последние, чтобы повторное включение не требовало повторного ввода.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.application.plan_pricing import invalidate_all_plan_pricing
from app.domain.plans import get_all_plans
from app.domain.pricing import validate_global_discount
from app.domain.promo import FIXED_PLAN_IDS, PromoDiscountKind
from app.persistence.postgres_pricing import PostgresPlanPricingRepository
from app.web_api.admin_auth import _admin_actor, _with_admin_auth, _with_admin_csrf
from app.web_api.helpers import safe_json_error

_LOGGER = logging.getLogger(__name__)

# Границы цены — зеркалят CHECK миграции 076 (plan_price_overrides).
_PRICE_MIN_RUBLES = 1
_PRICE_MAX_RUBLES = 100_000


def _gd_descriptor(raw: dict) -> dict:
    """Сырая строка синглона (kind/value могут быть NULL при выключенной) → JSON."""
    return {
        "is_active": raw.get("is_active", False),
        "discount_kind": raw.get("discount_kind"),
        "discount_value": raw.get("discount_value"),
        "public_label": raw.get("public_label"),
        "updated_by": raw.get("updated_by"),
        "updated_at": raw["updated_at"].isoformat() if raw.get("updated_at") else None,
    }


@_with_admin_auth
async def handle_admin_list_plans(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    try:
        repo = PostgresPlanPricingRepository(pool)
        # Один запрос даёт и {plan_id: цена}, и метки updated_by/at оверрайдов.
        override_rows = await pool.fetch(
            "SELECT plan_id, price_rubles, updated_by, updated_at FROM plan_price_overrides ORDER BY plan_id"
        )
        row_by_plan = {row["plan_id"]: row for row in override_rows}
        plans = []
        for plan in get_all_plans():
            pid = plan.plan_id.value
            row = row_by_plan.get(pid)
            plans.append(
                {
                    "plan_id": pid,
                    "duration_days": plan.duration_days,
                    "default_price_rubles": plan.price_rubles,
                    "price_rubles": row["price_rubles"] if row else plan.price_rubles,
                    "is_overridden": row is not None,
                    "updated_by": row["updated_by"] if row else None,
                    "updated_at": row["updated_at"].isoformat() if row and row["updated_at"] else None,
                }
            )
        return JSONResponse(
            {
                "ok": True,
                "plans": plans,
                "global_discount": _gd_descriptor(await repo.get_global_discount_raw()),
            }
        )
    except Exception:
        _LOGGER.exception("admin_plans_list_error")
        return safe_json_error(500, "internal_error")


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_set_plan_price(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    plan_id = request.path_params["plan_id"]
    if plan_id not in FIXED_PLAN_IDS:
        return safe_json_error(404, "plan_not_found")
    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request", "Expected JSON body")
    raw_price = data.get("price_rubles") if isinstance(data, dict) else None
    if isinstance(raw_price, bool) or not isinstance(raw_price, int):
        return safe_json_error(400, "invalid_price")
    if not (_PRICE_MIN_RUBLES <= raw_price <= _PRICE_MAX_RUBLES):
        return safe_json_error(400, "price_out_of_range")

    try:
        repo = PostgresPlanPricingRepository(pool)
        old = await repo.get_plan_price(plan_id)
        row = await repo.upsert_plan_price(plan_id=plan_id, price_rubles=raw_price, actor=_admin_actor(request))
        await repo.record_action(
            actor=_admin_actor(request),
            action="set_plan_price",
            plan_id=plan_id,
            old_value=old,
            new_value=row,
            correlation_id=f"admin-pricing-{uuid.uuid4()}",
        )
        invalidate_all_plan_pricing()
        return JSONResponse(
            {
                "ok": True,
                "plan": {
                    "plan_id": row["plan_id"],
                    "price_rubles": row["price_rubles"],
                    "updated_by": row["updated_by"],
                    "updated_at": row["updated_at"].isoformat(),
                },
            }
        )
    except Exception:
        _LOGGER.exception("admin_plan_price_set_error")
        return safe_json_error(500, "internal_error")


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_reset_plan_price(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    plan_id = request.path_params["plan_id"]
    if plan_id not in FIXED_PLAN_IDS:
        return safe_json_error(404, "plan_not_found")
    try:
        repo = PostgresPlanPricingRepository(pool)
        old = await repo.get_plan_price(plan_id)
        if old is None:
            return safe_json_error(404, "no_override")
        deleted = await repo.delete_plan_price(plan_id)
        if deleted:
            await repo.record_action(
                actor=_admin_actor(request),
                action="reset_plan_price",
                plan_id=plan_id,
                old_value=old,
                new_value=None,
                correlation_id=f"admin-pricing-{uuid.uuid4()}",
            )
            invalidate_all_plan_pricing()
        default_price = next(
            (p.price_rubles for p in get_all_plans() if p.plan_id.value == plan_id),
            None,
        )
        return JSONResponse({"ok": True, "plan_id": plan_id, "price_rubles": default_price})
    except Exception:
        _LOGGER.exception("admin_plan_price_reset_error")
        return safe_json_error(500, "internal_error")


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_set_global_discount(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request", "Expected JSON body")
    if not isinstance(data, dict) or not isinstance(data.get("is_active"), bool):
        return safe_json_error(400, "invalid_is_active")
    is_active: bool = data["is_active"]

    try:
        repo = PostgresPlanPricingRepository(pool)
        old_raw = await repo.get_global_discount_raw()

        kind_raw = data.get("discount_kind")
        value_raw = data.get("discount_value")
        public_label = data.get("public_label")
        if not is_active and kind_raw is None and value_raw is None:
            # Выключение без параметров — сохраняем последние kind/value/label
            # (лёгкое повторное включение без повторного ввода).
            kind_raw = old_raw.get("discount_kind")
            value_raw = old_raw.get("discount_value")
            if public_label is None:
                public_label = old_raw.get("public_label")

        discount_kind: PromoDiscountKind | None = None
        discount_value: int | None = None
        if is_active:
            try:
                discount_kind = PromoDiscountKind(kind_raw)
            except ValueError:
                return safe_json_error(400, "invalid_discount_kind")
            if isinstance(value_raw, bool) or not isinstance(value_raw, int):
                return safe_json_error(400, "invalid_discount_value")
            if (err := validate_global_discount(discount_kind, value_raw)) is not None:
                return safe_json_error(400, "invalid_discount", err)
            discount_value = value_raw
        else:
            # Хранение выключенного состояния: валидируем только если передали.
            if kind_raw is not None:
                try:
                    discount_kind = PromoDiscountKind(kind_raw)
                except ValueError:
                    return safe_json_error(400, "invalid_discount_kind")
            if value_raw is not None:
                if isinstance(value_raw, bool) or not isinstance(value_raw, int):
                    return safe_json_error(400, "invalid_discount_value")
                discount_value = value_raw

        if public_label is not None and not isinstance(public_label, str):
            return safe_json_error(400, "invalid_public_label")
        if public_label == "":
            public_label = None

        row = await repo.set_global_discount(
            is_active=is_active,
            discount_kind=discount_kind.value if discount_kind is not None else None,
            discount_value=discount_value,
            public_label=public_label,
            actor=_admin_actor(request),
        )
        await repo.record_action(
            actor=_admin_actor(request),
            action="set_global_discount",
            plan_id=None,
            old_value=old_raw or None,
            new_value=row,
            correlation_id=f"admin-pricing-{uuid.uuid4()}",
        )
        invalidate_all_plan_pricing()
        return JSONResponse({"ok": True, "global_discount": _gd_descriptor(row)})
    except ValueError as exc:
        # CHECK миграции 076: активная скидка без kind/value и т.п.
        if str(exc) == "global_discount_inconsistent":
            return safe_json_error(400, "global_discount_inconsistent")
        _LOGGER.exception("admin_global_discount_set_error")
        return safe_json_error(500, "internal_error")
    except Exception:
        _LOGGER.exception("admin_global_discount_set_error")
        return safe_json_error(500, "internal_error")
