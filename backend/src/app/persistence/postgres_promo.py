"""PostgreSQL-персистентность промокодов: коды, журнал погашений, применённые.

Шаблон зеркалирует postgres_referral.py: один класс на репозиторий, ctor принимает
pool, атомарные операции реализованы как @staticmethod *_in_connection для запуска
внутри общей транзакции погашения. SQL — статические литералы (без интерполяции и
без конкатенации с переменными), как в эталоне; единственная динамическая часть
(whitelist-UPDATE) помечена noqa: S608.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import asyncpg

from app.domain.promo import PromoCode
from app.persistence.promo_contracts import (
    PromoAppliedRecord,
    PromoRedemptionRecord,
    record_from_row,
)

# Стоп-лист колонок, которые админ может менять через update() (защита от SQL-инъекций
# при динамической генерации SET). redemptions_count/created_at/created_by не входят.
_UPDATABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "audience",
        "discount_kind",
        "discount_value",
        "max_redemptions",
        "expires_at",
        "is_active",
        "note",
    }
)


class PostgresPromoCodeRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, code: str) -> PromoCode | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT code, audience, discount_kind, discount_value, max_redemptions, "
                "redemptions_count, expires_at, is_active, note, created_by, created_at, updated_at "
                "FROM promo_codes WHERE code = $1",
                code,
            )
        return None if row is None else record_from_row(row)

    async def create(self, promo: PromoCode) -> PromoCode:
        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    "INSERT INTO promo_codes "
                    "(code, audience, discount_kind, discount_value, max_redemptions, "
                    "redemptions_count, expires_at, is_active, note, created_by) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
                    "RETURNING code, audience, discount_kind, discount_value, max_redemptions, "
                    "redemptions_count, expires_at, is_active, note, created_by, created_at, updated_at",
                    promo.code,
                    promo.audience.value,
                    promo.discount_kind.value,
                    promo.discount_value,
                    promo.max_redemptions,
                    promo.redemptions_count,
                    promo.expires_at,
                    promo.is_active,
                    promo.note,
                    promo.created_by,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ValueError("promo_code_exists") from exc
        assert row is not None
        return record_from_row(row)

    async def update(self, code: str, changes: dict[str, Any]) -> PromoCode | None:
        safe = {k: v for k, v in changes.items() if k in _UPDATABLE_COLUMNS}
        if not safe:
            return await self.get(code)
        # Колонки SET берутся только из белого списка _UPDATABLE_COLUMNS; значения параметризованы ($N).
        assignments = [f"{col} = ${i + 2}" for i, col in enumerate(safe.keys())]
        assignments.append("updated_at = now()")
        params: list[Any] = [code, *safe.values()]
        sql = (
            "UPDATE promo_codes SET "  # noqa: S608 -- белый список _UPDATABLE_COLUMNS, значения параметризованы ($N)
            + ", ".join(assignments)
            + " WHERE code = $1 RETURNING code, audience, discount_kind, discount_value, max_redemptions, redemptions_count, expires_at, is_active, note, created_by, created_at, updated_at"
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        return None if row is None else record_from_row(row)

    async def delete(self, code: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM promo_codes WHERE code = $1", code)
        return "DELETE 1" in result

    async def list_codes(
        self, *, limit: int = 100, offset: int = 0, include_inactive: bool = True
    ) -> tuple[PromoCode, ...]:
        async with self._pool.acquire() as conn:
            if include_inactive:
                rows = await conn.fetch(
                    "SELECT code, audience, discount_kind, discount_value, max_redemptions, "
                    "redemptions_count, expires_at, is_active, note, created_by, created_at, updated_at "
                    "FROM promo_codes ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                    limit,
                    offset,
                )
            else:
                rows = await conn.fetch(
                    "SELECT code, audience, discount_kind, discount_value, max_redemptions, "
                    "redemptions_count, expires_at, is_active, note, created_by, created_at, updated_at "
                    "FROM promo_codes WHERE is_active = TRUE "
                    "ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                    limit,
                    offset,
                )
        return tuple(record_from_row(r) for r in rows)

    @staticmethod
    async def increment_redemptions_in_connection(
        conn: asyncpg.Connection, code: str, max_redemptions: int | None
    ) -> bool:
        """+1 к redemptions_count, если код существует и лимит не исчерпан."""
        result = await conn.execute(
            "UPDATE promo_codes "
            "SET redemptions_count = redemptions_count + 1, updated_at = now() "
            "WHERE code = $1 AND ($2::int IS NULL OR redemptions_count < $2::int)",
            code,
            max_redemptions,
        )
        return "UPDATE 1" in result


class PostgresPromoRedemptionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_by_user(self, internal_user_id: str, limit: int = 20) -> tuple[PromoRedemptionRecord, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT redemption_id, code, internal_user_id, idempotency_key, "
                "base_rubles, discounted_rubles, discount_kopecks, payment_ref, created_at "
                "FROM promo_redemptions WHERE internal_user_id = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                internal_user_id,
                limit,
            )
        return tuple(_redemption_from_row(r) for r in rows)

    @staticmethod
    async def redeem_in_connection(conn: asyncpg.Connection, record: PromoRedemptionRecord) -> bool:
        """Идемпотентная вставка погашения (True — вставлено, False — дубль/один-на-юзера)."""
        result = await conn.execute(
            "INSERT INTO promo_redemptions "
            "(redemption_id, code, internal_user_id, idempotency_key, "
            "base_rubles, discounted_rubles, discount_kopecks, payment_ref, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
            "ON CONFLICT DO NOTHING",
            record.redemption_id,
            record.code,
            record.internal_user_id,
            record.idempotency_key,
            record.base_rubles,
            record.discounted_rubles,
            record.discount_kopecks,
            record.payment_ref,
            record.created_at,
        )
        return "INSERT 0 1" in result


class PostgresPromoAppliedRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, internal_user_id: str) -> PromoAppliedRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT internal_user_id, code, applied_at, expires_at FROM promo_applied WHERE internal_user_id = $1",
                internal_user_id,
            )
        return None if row is None else _applied_from_row(row)

    async def upsert(self, internal_user_id: str, code: str, expires_at: datetime) -> PromoAppliedRecord:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO promo_applied (internal_user_id, code, applied_at, expires_at) "
                "VALUES ($1, $2, now(), $3) "
                "ON CONFLICT (internal_user_id) DO UPDATE "
                "SET code = EXCLUDED.code, applied_at = now(), expires_at = EXCLUDED.expires_at "
                "RETURNING internal_user_id, code, applied_at, expires_at",
                internal_user_id,
                code,
                expires_at,
            )
        assert row is not None
        return _applied_from_row(row)

    async def clear(self, internal_user_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM promo_applied WHERE internal_user_id = $1",
                internal_user_id,
            )


def _redemption_from_row(row: Any) -> PromoRedemptionRecord:
    return PromoRedemptionRecord(
        redemption_id=row["redemption_id"],
        code=row["code"],
        internal_user_id=row["internal_user_id"],
        idempotency_key=row["idempotency_key"],
        base_rubles=int(row["base_rubles"]),
        discounted_rubles=int(row["discounted_rubles"]),
        discount_kopecks=int(row["discount_kopecks"]),
        payment_ref=row["payment_ref"],
        created_at=row["created_at"],
    )


def _applied_from_row(row: Any) -> PromoAppliedRecord:
    return PromoAppliedRecord(
        internal_user_id=row["internal_user_id"],
        code=row["code"],
        applied_at=row["applied_at"],
        expires_at=row["expires_at"],
    )


def new_redemption_id() -> str:
    return str(uuid.uuid4())
