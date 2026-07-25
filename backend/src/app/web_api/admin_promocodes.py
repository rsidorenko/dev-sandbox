"""Админ-эндпоинты вкладки «Промокоды»: список + CRUD (create/update/delete)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import asyncpg
from asyncpg.exceptions import CheckViolationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.promo import (
    FIXED_PLAN_IDS,
    PromoAudience,
    PromoCode,
    PromoDiscountKind,
    normalize_code,
    validate_code_format,
    validate_discount,
    validate_discounts_by_plan,
)
from app.persistence.postgres_promo import PostgresPromoCodeRepository
from app.web_api.admin_auth import _admin_actor, _with_admin_auth, _with_admin_csrf
from app.web_api.helpers import safe_json_error

_LOGGER = logging.getLogger(__name__)

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


def _promo_to_dict(promo: PromoCode) -> dict:
    return {
        "code": promo.code,
        "audience": promo.audience.value,
        "discount_kind": promo.discount_kind.value,
        "discount_value": promo.discount_value,
        "discounts_by_plan": promo.discounts_by_plan,
        "max_redemptions": promo.max_redemptions,
        "redemptions_count": promo.redemptions_count,
        "expires_at": promo.expires_at.isoformat() if promo.expires_at else None,
        "is_active": promo.is_active,
        "note": promo.note,
        "created_at": promo.created_at.isoformat() if promo.created_at else None,
        "updated_at": promo.updated_at.isoformat() if promo.updated_at else None,
        "created_by": promo.created_by,
    }


def _parse_expires_at(raw) -> datetime | None:
    if raw is None or raw == "":
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid_expires_at") from exc


def _coerce_discounts_by_plan(raw: object) -> dict[str, int] | None:
    """JSON-объект {plan_id: int} → dict, либо None. Бросает ValueError при ошибке формата."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, dict):
        raise ValueError("invalid_discounts_by_plan")
    mapping: dict[str, int] = {}
    for plan_id, value in raw.items():
        if not isinstance(plan_id, str) or plan_id not in FIXED_PLAN_IDS:
            raise ValueError("invalid_discounts_by_plan")
        if isinstance(value, bool) or not isinstance(value, int):
            # JSON-числа приходят как int; дробные/строки — ошибка.
            raise ValueError("invalid_discounts_by_plan")
        mapping[plan_id] = value
    return mapping


def _parse_int_optional(raw, *, field: str, allow_none: bool = True) -> int | None:
    if raw is None or raw == "":
        if allow_none:
            return None
        raise ValueError(f"missing_{field}")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{field}") from exc


@_with_admin_auth
async def handle_admin_list_promocodes(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    try:
        limit = max(1, min(_MAX_LIMIT, int(request.query_params.get("limit", _DEFAULT_LIMIT))))
    except ValueError:
        limit = _DEFAULT_LIMIT
    try:
        offset = max(0, int(request.query_params.get("offset", "0")))
    except ValueError:
        offset = 0
    include_inactive = request.query_params.get("active", "").strip().lower() not in ("0", "false", "no")

    repo = PostgresPromoCodeRepository(pool)
    codes = await repo.list_codes(limit=limit, offset=offset, include_inactive=include_inactive)
    return JSONResponse(
        {"ok": True, "promocodes": [_promo_to_dict(c) for c in codes], "limit": limit, "offset": offset}
    )


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_create_promocode(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request", "Expected JSON body")
    if not isinstance(data, dict):
        return safe_json_error(400, "invalid_request", "Expected JSON object")

    code = normalize_code(data.get("code") or "")
    if (err := validate_code_format(code)) is not None:
        return safe_json_error(400, "invalid_code", err)

    try:
        audience = PromoAudience(data.get("audience"))
        discount_kind = PromoDiscountKind(data.get("discount_kind"))
    except ValueError:
        return safe_json_error(400, "invalid_kind")

    # Две взаимоисключающие моды: плоская (discount_value) или per-tariff (discounts_by_plan).
    if data.get("discount_value") not in (None, "") and data.get("discounts_by_plan") not in (None, ""):
        return safe_json_error(400, "discount_mode_conflict", "Передайте либо discount_value, либо discounts_by_plan")
    try:
        discounts_by_plan = _coerce_discounts_by_plan(data.get("discounts_by_plan"))
    except ValueError:
        return safe_json_error(400, "invalid_discounts_by_plan")
    if discounts_by_plan is not None:
        if (err := validate_discounts_by_plan(discount_kind, discounts_by_plan)) is not None:
            return safe_json_error(400, "invalid_discounts_by_plan", err)
        discount_value = 0  # не используется в per-tariff моде; в БД пишем NULL
    else:
        raw_value = data.get("discount_value")
        # Строго целое (как в per-tariff): int() молча режет float и принимает bool.
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            return safe_json_error(400, "invalid_discount_value")
        discount_value = raw_value
        if (err := validate_discount(discount_kind, discount_value)) is not None:
            return safe_json_error(400, "invalid_discount_value", err)

    try:
        max_redemptions = _parse_int_optional(data.get("max_redemptions"), field="max_redemptions")
    except ValueError as exc:
        return safe_json_error(400, str(exc))
    if max_redemptions is not None and max_redemptions <= 0:
        return safe_json_error(400, "invalid_max_redemptions")

    try:
        expires_at = _parse_expires_at(data.get("expires_at"))
    except ValueError:
        return safe_json_error(400, "invalid_expires_at")

    note = data.get("note")
    if note is not None:
        note = str(note)[:500]
    is_active = bool(data.get("is_active", True))

    now = datetime.now(UTC)
    promo = PromoCode(
        code=code,
        audience=audience,
        discount_kind=discount_kind,
        discount_value=discount_value,
        max_redemptions=max_redemptions,
        redemptions_count=0,
        expires_at=expires_at,
        is_active=is_active,
        note=note,
        created_at=now,
        updated_at=now,
        created_by=_admin_actor(request),
        discounts_by_plan=discounts_by_plan,
    )

    repo = PostgresPromoCodeRepository(pool)
    try:
        created = await repo.create(promo)
    except ValueError:
        return safe_json_error(409, "promo_code_exists")
    except CheckViolationError:
        # На случай комбинации kind/value, не пойманной ап-фронт-валидацией (напр. percent>100).
        return safe_json_error(400, "invalid_discount_value", "DB CHECK constraint violated")
    except Exception:
        _LOGGER.exception("admin_promocode_create_error")
        return safe_json_error(500, "internal_error")
    return JSONResponse({"ok": True, "promocode": _promo_to_dict(created)}, status_code=201)


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_update_promocode(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    code = normalize_code(request.path_params["code"])
    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request", "Expected JSON body")
    if not isinstance(data, dict):
        return safe_json_error(400, "invalid_request", "Expected JSON object")

    repo = PostgresPromoCodeRepository(pool)
    changes: dict = {}
    if "audience" in data:
        try:
            changes["audience"] = PromoAudience(data["audience"]).value
        except ValueError:
            return safe_json_error(400, "invalid_kind")
    if "discount_kind" in data:
        try:
            changes["discount_kind"] = PromoDiscountKind(data["discount_kind"]).value
        except ValueError:
            return safe_json_error(400, "invalid_kind")
    effective_kind = PromoDiscountKind(changes["discount_kind"]) if "discount_kind" in changes else None

    # Моды взаимоисключаемы.
    if data.get("discount_value") not in (None, "") and data.get("discounts_by_plan") not in (None, ""):
        return safe_json_error(400, "discount_mode_conflict", "Передайте либо discount_value, либо discounts_by_plan")

    # Для percent-валидации нужен итоговый kind: из запроса, иначе текущий из БД (один fetch).
    kind_for_check = effective_kind
    if kind_for_check is None and ("discounts_by_plan" in data or "discount_value" in data):
        current = await repo.get(code)
        if current is None:
            return safe_json_error(404, "promo_code_not_found")
        kind_for_check = current.discount_kind

    # per-tariff скидки.
    if "discounts_by_plan" in data:
        try:
            mapping = _coerce_discounts_by_plan(data["discounts_by_plan"])
        except ValueError:
            return safe_json_error(400, "invalid_discounts_by_plan")
        if mapping:  # непустой map; {} (falsy) → ветка сброса
            if (err := validate_discounts_by_plan(kind_for_check, mapping)) is not None:
                return safe_json_error(400, "invalid_discounts_by_plan", err)
            changes["discounts_by_plan"] = mapping
            changes["discount_value"] = None  # переключение на per-tariff → обнулить плоскую (NULL)
        else:
            changes["discounts_by_plan"] = None  # явный сброс per-tariff (вкл. пустой {})

    if "discount_value" in data:
        raw_value = data["discount_value"]
        # Строго целое (как в per-tariff): int() молча режет float и принимает bool.
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            return safe_json_error(400, "invalid_discount_value")
        if kind_for_check is not None and validate_discount(kind_for_check, raw_value) is not None:
            return safe_json_error(400, "invalid_discount_value")
        if raw_value <= 0:
            return safe_json_error(400, "invalid_discount_value")
        changes["discount_value"] = raw_value
        changes["discounts_by_plan"] = None  # переключение на плоскую → обнулить per-tariff
    if "max_redemptions" in data:
        try:
            changes["max_redemptions"] = _parse_int_optional(data["max_redemptions"], field="max_redemptions")
        except ValueError as exc:
            return safe_json_error(400, str(exc))
        if changes["max_redemptions"] is not None and changes["max_redemptions"] <= 0:
            return safe_json_error(400, "invalid_max_redemptions")
    if "expires_at" in data:
        try:
            changes["expires_at"] = _parse_expires_at(data["expires_at"])
        except ValueError:
            return safe_json_error(400, "invalid_expires_at")
    if "is_active" in data:
        changes["is_active"] = bool(data["is_active"])
    if "note" in data:
        changes["note"] = None if data["note"] is None else str(data["note"])[:500]

    try:
        updated = await repo.update(code, changes)
    except CheckViolationError:
        # Напр. смена discount_kind на percent без перепроверки существующего value (>100).
        # Ап-фронт-валидация пропускает этот редкий случай — отдаём чистый 400, не 500.
        return safe_json_error(400, "invalid_discount_value", "DB CHECK constraint violated")
    except Exception:
        _LOGGER.exception("admin_promocode_update_error")
        return safe_json_error(500, "internal_error")
    if updated is None:
        return safe_json_error(404, "promo_code_not_found")
    return JSONResponse({"ok": True, "promocode": _promo_to_dict(updated)})


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_delete_promocode(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    code = normalize_code(request.path_params["code"])
    repo = PostgresPromoCodeRepository(pool)
    deleted = await repo.delete(code)
    if not deleted:
        return safe_json_error(404, "promo_code_not_found")
    return JSONResponse({"ok": True})
