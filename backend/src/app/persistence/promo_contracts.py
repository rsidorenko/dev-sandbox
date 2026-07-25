"""Контракты персистентности промокодов (Protocols, без конкретной БД)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.domain.promo import PromoAudience, PromoCode, PromoDiscountKind


@dataclass(frozen=True, slots=True)
class PromoRedemptionRecord:
    """Строка журнала погашений (append-only)."""

    redemption_id: str
    code: str
    internal_user_id: str
    idempotency_key: str
    base_rubles: int
    discounted_rubles: int
    discount_kopecks: int
    payment_ref: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PromoAppliedRecord:
    """Применённый (не погашенный) промокод пользователя."""

    internal_user_id: str
    code: str
    applied_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


class PromoCodeRepository(Protocol):
    """CRUD определений промокодов.

    Guarded-инкремент счётчика погашений реализован как Postgres-specific staticmethod
    `PostgresPromoCodeRepository.increment_redemptions_in_connection` (внутри общей
    транзакции погашения) и в Protocol не входит — как *_in_connection в referral.
    """

    async def get(self, code: str) -> PromoCode | None: ...
    async def create(self, promo: PromoCode) -> PromoCode: ...
    async def update(self, code: str, changes: dict[str, Any]) -> PromoCode | None: ...
    async def delete(self, code: str) -> bool: ...
    async def list_codes(
        self, *, limit: int = 100, offset: int = 0, include_inactive: bool = True
    ) -> tuple[PromoCode, ...]: ...


class PromoRedemptionRepository(Protocol):
    async def list_by_user(self, internal_user_id: str, limit: int = 20) -> tuple[PromoRedemptionRecord, ...]: ...

    # Идемпотентная вставка погашения — Postgres-specific staticmethod
    # `PostgresPromoRedemptionRepository.redeem_in_connection` (UNIQUE по idempotency_key
    # и по (code, internal_user_id)). В Protocol не входит.


class PromoAppliedRepository(Protocol):
    """Хранилище «намерений» — применённых промокодов (session-подобное состояние в БД)."""

    async def get(self, internal_user_id: str) -> PromoAppliedRecord | None: ...
    async def upsert(self, internal_user_id: str, code: str, expires_at: datetime) -> PromoAppliedRecord: ...
    async def clear(self, internal_user_id: str) -> None: ...


def _coerce_discounts_by_plan(raw: Any) -> dict[str, int] | None:
    """JSONB → dict[str,int] | None. asyncpg без jsonb-кодека отдаёт str; dict — если
    кодек зарегистрирован; None/пусто — плоская мода."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = json.loads(raw) if raw else None
    if not raw:
        return None
    return {str(k): int(v) for k, v in raw.items()}


def record_from_row(row: Any) -> PromoCode:
    """Сборка PromoCode из строки БД (asyncpg Record / dict-like)."""
    # discounts_by_plan может отсутствовать у старых SELECT — tolerant к KeyError.
    try:
        discounts_raw = row["discounts_by_plan"]
    except KeyError:
        discounts_raw = None
    return PromoCode(
        code=row["code"],
        audience=PromoAudience(row["audience"]),
        discount_kind=PromoDiscountKind(row["discount_kind"]),
        discount_value=int(row["discount_value"]) if row["discount_value"] is not None else 0,
        max_redemptions=None if row["max_redemptions"] is None else int(row["max_redemptions"]),
        redemptions_count=int(row["redemptions_count"]),
        expires_at=row["expires_at"],
        is_active=bool(row["is_active"]),
        note=row["note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by=row["created_by"],
        discounts_by_plan=_coerce_discounts_by_plan(discounts_raw),
    )


__all__ = [
    "PromoAppliedRecord",
    "PromoAppliedRepository",
    "PromoCodeRepository",
    "PromoRedemptionRecord",
    "PromoRedemptionRepository",
    "record_from_row",
]
