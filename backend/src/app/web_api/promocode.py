"""Web API promocode endpoints — показать / применить / снять закреплённый промокод.

Тонкая точка ввода-отображения поверх готовой логики Фазы 3
(``app.application.promo_service`` + ``domain.promo``). Здесь НЕТ математики скидки
и сверки: каноническая повторная валидация и расчёт живут в ``resolve_active_promo`` /
``compute_is_new_user`` и переиспользуются веб-платежами и ботом. После успешной
оплаты YooKassa-вебхук очищает ``promo_applied`` (Фаза 3), поэтому GET после checkout
вернёт ``{applied: null}``.

Веб-сессия (кука ``session``, НЕ админская) — те же ``require_auth`` + ``_with_auth``,
что и остальные ``/api/v1/user/*``. Запись (apply/clear) дополнительно за CSRF.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.promo import (
    PROMO_APPLIED_TTL_HOURS,
    PromoCode,
    PromoDiscountKind,
    audience_eligible,
    normalize_code,
    redeem_block_reason,
    validate_code_format,
)
from app.web_api.helpers import safe_json_error
from app.web_api.middleware import require_auth

_LOGGER = logging.getLogger(__name__)

# redeem_block_reason → user-facing код ошибки (409). Единый алфавит с ботом Фазы 3.
_BLOCK_ERROR: dict[str, str] = {
    "promo_inactive": "ineligible",
    "promo_expired": "expired",
    "promo_exhausted": "exhausted",
}


def _discount_label(promo: PromoCode) -> str:
    if promo.discount_kind == PromoDiscountKind.PERCENT:
        return f"−{promo.discount_value}%"
    return f"−{promo.discount_value} ₽"


def _remaining_hours(expires_at: datetime) -> int:
    delta = expires_at - datetime.now(UTC)
    return max(0, int(delta.total_seconds() // 3600))


def _applied_descriptor(promo: PromoCode, *, expires_at: datetime, valid: bool) -> dict:
    return {
        "code": promo.code,
        "discount_kind": promo.discount_kind.value,
        "discount_value": promo.discount_value,
        "discount_label": _discount_label(promo),
        "expires_at": expires_at.isoformat(),
        "remaining_hours": _remaining_hours(expires_at),
        "valid": valid,
    }


async def _require_identity(request: Request) -> tuple[asyncpg.Pool, str] | JSONResponse:
    """Auth + resolve internal_user_id. Web-only (email) accounts carry a non-positive
    telegram_user_id and are resolved by the same DB lookup as profile/payment."""
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result
    telegram_user_id = auth_result.get("telegram_user_id")
    if telegram_user_id is None:
        return safe_json_error(403, "no_telegram_identity")
    pool: asyncpg.Pool = request.app.state.pool
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return safe_json_error(404, "identity_not_found")
    return pool, identity["internal_user_id"]


async def handle_get_promocode(request: Request) -> JSONResponse:
    """Текущий применённый промокод (TTL 24ч) + дескриптор скидки, либо {applied: null}."""
    try:
        auth_result = await require_auth(request)
        if isinstance(auth_result, JSONResponse):
            return auth_result
        telegram_user_id = auth_result.get("telegram_user_id")
        if telegram_user_id is None:
            return safe_json_error(403, "no_telegram_identity")
        pool: asyncpg.Pool = request.app.state.pool
        identity = await pool.fetchrow(
            "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
            telegram_user_id,
        )
        # Нет идентичности — нет и промокода; отдаём пустое состояние (fail-soft для кабинета).
        if identity is None:
            return JSONResponse({"ok": True, "applied": None})
        internal_user_id = identity["internal_user_id"]

        from app.application.promo_service import compute_is_new_user
        from app.persistence.postgres_promo import (
            PostgresPromoAppliedRepository,
            PostgresPromoCodeRepository,
        )

        applied_repo = PostgresPromoAppliedRepository(pool)
        code_repo = PostgresPromoCodeRepository(pool)
        applied = await applied_repo.get(internal_user_id)
        now = datetime.now(UTC)
        if applied is None or applied.is_expired(now):
            return JSONResponse({"ok": True, "applied": None})

        promo = await code_repo.get(applied.code)
        if promo is None:
            # Намерение указывает на удалённый код → считаем, что промокода нет (fail-closed).
            return JSONResponse({"ok": True, "applied": None})

        valid = redeem_block_reason(promo, now) is None
        if valid:
            is_new = await compute_is_new_user(pool=pool, snapshots=None, internal_user_id=internal_user_id)
            valid = audience_eligible(promo.audience, is_new_user=is_new)
        return JSONResponse(
            {"ok": True, "applied": _applied_descriptor(promo, expires_at=applied.expires_at, valid=valid)}
        )
    except Exception:
        _LOGGER.exception("promocode_get_error")
        return safe_json_error(500, "internal_error")


async def handle_apply_promocode(request: Request) -> JSONResponse:
    """Применить промокод: normalize → re-валидация (активность/срок/лимит/аудитория)
    → upsert в promo_applied (TTL 24ч). Опц. plan_id+device_count → preview скидки."""
    try:
        resolved = await _require_identity(request)
        if isinstance(resolved, JSONResponse):
            return resolved
        pool, internal_user_id = resolved

        try:
            data = await request.json()
        except Exception:
            return safe_json_error(400, "invalid_request")
        code = normalize_code(str(data.get("code", "")))
        if validate_code_format(code) is not None:
            return safe_json_error(400, "invalid_format")

        from app.application.promo_service import compute_is_new_user
        from app.persistence.postgres_promo import (
            PostgresPromoAppliedRepository,
            PostgresPromoCodeRepository,
        )

        applied_repo = PostgresPromoAppliedRepository(pool)
        code_repo = PostgresPromoCodeRepository(pool)

        promo = await code_repo.get(code)
        if promo is None:
            return safe_json_error(404, "not_found")

        now = datetime.now(UTC)
        block = redeem_block_reason(promo, now)
        if block is not None:
            return safe_json_error(409, _BLOCK_ERROR.get(block, "ineligible"))

        is_new = await compute_is_new_user(pool=pool, snapshots=None, internal_user_id=internal_user_id)
        if not audience_eligible(promo.audience, is_new_user=is_new):
            return safe_json_error(409, "wrong_audience")

        expires_at = now + timedelta(hours=PROMO_APPLIED_TTL_HOURS)
        await applied_repo.upsert(internal_user_id, code, expires_at)

        body: dict = {"ok": True, "applied": _applied_descriptor(promo, expires_at=expires_at, valid=True)}

        # Preview скидки против конкретного тарифа — страница оплаты передаёт plan+devices.
        plan_id = str(data.get("plan_id", "")).strip()
        if plan_id:
            from app.domain.plans import calculate_total_price, get_plan
            from app.domain.promo import apply_promo_discount

            plan = get_plan(plan_id)
            if plan is not None:
                try:
                    device_count = int(data.get("device_count", 5))
                except (ValueError, TypeError):
                    device_count = 5
                base = calculate_total_price(plan, device_count)
                price = apply_promo_discount(base, promo)
                body["preview"] = {
                    "plan_id": plan_id,
                    "device_count": device_count,
                    "base_rubles": price.base_rubles,
                    "discounted_rubles": price.discounted_rubles,
                    "discount_kopecks": price.discount_kopecks,
                }
        return JSONResponse(body)
    except Exception:
        _LOGGER.exception("promocode_apply_error")
        return safe_json_error(500, "internal_error")


async def handle_clear_promocode(request: Request) -> JSONResponse:
    """Снять закреплённый промокод (раннее снятие до истечения 24ч / до оплаты)."""
    try:
        resolved = await _require_identity(request)
        if isinstance(resolved, JSONResponse):
            return resolved
        pool, internal_user_id = resolved
        from app.persistence.postgres_promo import PostgresPromoAppliedRepository

        await PostgresPromoAppliedRepository(pool).clear(internal_user_id)
        return JSONResponse({"ok": True, "applied": None})
    except Exception:
        _LOGGER.exception("promocode_clear_error")
        return safe_json_error(500, "internal_error")
