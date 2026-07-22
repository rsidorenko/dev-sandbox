"""Админ-эндпоинты вкладки «Промокоды»: список + CRUD (create/update/delete)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.promo import (
    PromoAudience,
    PromoCode,
    PromoDiscountKind,
    normalize_code,
    validate_code_format,
    validate_discount,
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

    code = normalize_code(data.get("code") or "")
    if (err := validate_code_format(code)) is not None:
        return safe_json_error(400, "invalid_code", err)

    try:
        audience = PromoAudience(data.get("audience"))
        discount_kind = PromoDiscountKind(data.get("discount_kind"))
    except ValueError:
        return safe_json_error(400, "invalid_kind")

    try:
        discount_value = int(data.get("discount_value"))
    except (TypeError, ValueError):
        return safe_json_error(400, "invalid_discount_value")
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
    )

    repo = PostgresPromoCodeRepository(pool)
    try:
        created = await repo.create(promo)
    except ValueError:
        return safe_json_error(409, "promo_code_exists")
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
    if "discount_value" in data:
        kind = PromoDiscountKind(changes["discount_kind"]) if "discount_kind" in changes else None
        try:
            value = int(data["discount_value"])
        except (TypeError, ValueError):
            return safe_json_error(400, "invalid_discount_value")
        # Если kind не меняется в этом запросе — валидация пройдёт по текущему значению в БД;
        # базовый формат (>=1, percent<=100) проверим по известному kind, иначе только >0.
        if kind is not None and validate_discount(kind, value) is not None:
            return safe_json_error(400, "invalid_discount_value")
        if value <= 0:
            return safe_json_error(400, "invalid_discount_value")
        changes["discount_value"] = value
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

    repo = PostgresPromoCodeRepository(pool)
    updated = await repo.update(code, changes)
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
