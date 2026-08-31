"""Shared apply-ядро промокода — единая точка «применить промокод пользователю».

Дословный перенос ``bot_transport/runtime_facade.py::_apply_promo_to_user`` (его,
в свою очередь, дублировал inline-блок ``web_api/promocode.py::handle_apply_promocode``).
Потребители: бот (текстовый ввод + кнопка рассылки), веб (ручной ввод в кабинете,
клейм бонуса рассылки из уведомления).

Ядро НЕ трогает rate-limit / FSM / HTTP-маппинг / render — это остаётся в вызывающем
коде. Принимает уже-resolved internal_user_id. Скидка канонически пере-валидируется
при оплате (``resolve_active_promo``), поэтому здесь сохраняется лишь «намерение»
(promo_applied, TTL 24ч).

Repo-параметры (а не pool-only) — стиль ``resolve_active_promo``: in-memory тесты
бота работают без пула, веб строит Postgres-репозитории из pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import asyncpg

from app.application.promo_service import compute_is_new_user
from app.domain.promo import (
    PROMO_APPLIED_TTL_HOURS,
    PromoCode,
    audience_eligible,
    normalize_code,
    per_user_exhausted,
    redeem_block_reason,
    validate_code_format,
)
from app.persistence.promo_contracts import (
    PromoAppliedRepository,
    PromoCodeRepository,
    PromoRedemptionRepository,
)

if TYPE_CHECKING:
    from app.application.interfaces import SubscriptionSnapshotReader


@dataclass(frozen=True, slots=True)
class PromoApplyOutcome:
    """Результат apply-ядра.

    status ∈ {"ok", "invalid", "not_found", "inactive", "already_used", "ineligible"};
    promo — PromoCode при не-invalid/not_found; block_reason — конкретная причина
    ``redeem_block_reason`` при status="inactive" (веб отличает expired/exhausted,
    бот схлопывает все в одно сообщение).
    """

    status: str
    code: str | None = None
    promo: PromoCode | None = None
    block_reason: str | None = None


async def apply_promo_intent(
    *,
    pool: asyncpg.Pool | None,
    promo_code_repo: PromoCodeRepository,
    promo_redemption_repo: PromoRedemptionRepository,
    promo_applied_repo: PromoAppliedRepository,
    snapshots: SubscriptionSnapshotReader | None,
    internal_user_id: str,
    raw_code: str,
    now: datetime | None = None,
) -> PromoApplyOutcome:
    """normalize → get → redeem_block_reason → per-user → audience → upsert.

    already_used — per-user лимит исчерпан (код с max_redemptions_per_user=1 уже
    погашен этим пользователем). Идемпотентность — promo_applied PK=internal_user_id
    (повторный apply того же кода просто продлевает TTL намерения).
    """
    code = normalize_code(raw_code)
    if validate_code_format(code) is not None:
        return PromoApplyOutcome(status="invalid", code=code)

    promo = await promo_code_repo.get(code)
    if promo is None:
        return PromoApplyOutcome(status="not_found", code=code)

    now = now or datetime.now(UTC)
    block_reason = redeem_block_reason(promo, now)
    if block_reason is not None:
        # inactive/expired/exhausted — вызывающий решает, как показать (обычно «не найден»).
        return PromoApplyOutcome(status="inactive", code=code, promo=promo, block_reason=block_reason)

    used = await promo_redemption_repo.count_for_user(code, internal_user_id)
    if per_user_exhausted(promo, used):
        return PromoApplyOutcome(status="already_used", code=code, promo=promo)

    is_new = await compute_is_new_user(
        pool=pool,
        snapshots=snapshots,
        internal_user_id=internal_user_id,
    )
    if not audience_eligible(promo.audience, is_new_user=is_new):
        return PromoApplyOutcome(status="ineligible", code=code, promo=promo)

    expires_at = now + timedelta(hours=PROMO_APPLIED_TTL_HOURS)
    await promo_applied_repo.upsert(internal_user_id, code, expires_at)
    return PromoApplyOutcome(status="ok", code=code, promo=promo)
