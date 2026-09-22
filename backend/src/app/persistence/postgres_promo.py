"""PostgreSQL-персистентность промокодов: коды, журнал погашений, применённые.

Шаблон зеркалирует postgres_referral.py: один класс на репозиторий, ctor принимает
pool, атомарные операции реализованы как @staticmethod *_in_connection для запуска
внутри общей транзакции погашения. SQL — статические литералы (без интерполяции и
без конкатенации с переменными), как в эталоне; единственная динамическая часть
(whitelist-UPDATE) помечена noqa: S608.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
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
# discounts_by_plan требует ::jsonb-каста — обрабатывается отдельно в update().
_UPDATABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "audience",
        "discount_kind",
        "discount_value",
        "discounts_by_plan",
        "max_redemptions",
        "max_redemptions_per_user",
        "expires_at",
        "is_active",
        "note",
    }
)

# Колонки, возвращаемые всеми SELECT/RETURNING — единый список, чтобы не рассинхронить
# парсинг в record_from_row (там же читаются discounts_by_plan/max_redemptions_per_user).
_PROMO_SELECT_COLUMNS = (
    "code, audience, discount_kind, discount_value, discounts_by_plan, max_redemptions, "
    "max_redemptions_per_user, redemptions_count, expires_at, is_active, note, "
    "created_by, created_at, updated_at"
)


def _encode_discounts_by_plan(value: dict[str, int] | None) -> str | None:
    """dict → JSON-строка для записи в JSONB (кодек не зарегистрирован). None остаётся None."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value else None


@asynccontextmanager
async def _connection_scope(pool: asyncpg.Pool, connection: asyncpg.Connection | None):
    """Переданным соединением владеет вызывающая транзакция."""
    if connection is not None:
        yield connection
    else:
        async with pool.acquire() as acquired:
            yield acquired


class PostgresPromoCodeRepository:
    def __init__(self, pool: asyncpg.Pool, *, connection: asyncpg.Connection | None = None) -> None:
        self._pool = pool
        self._connection = connection

    async def get(self, code: str) -> PromoCode | None:
        async with _connection_scope(self._pool, self._connection) as conn:
            row = await conn.fetchrow(
                f"SELECT {_PROMO_SELECT_COLUMNS} FROM promo_codes WHERE code = $1",  # noqa: S608
                code,
            )
        return None if row is None else record_from_row(row)

    async def create(self, promo: PromoCode) -> PromoCode:
        # per-tariff промокод хранит скидки в discounts_by_plan, а discount_value = NULL
        # (CHECK >0 не пустит 0; в домене discount_value=0, в БД пишем NULL).
        discount_value_db = None if promo.discounts_by_plan else promo.discount_value
        async with _connection_scope(self._pool, self._connection) as conn:
            try:
                row = await conn.fetchrow(
                    "INSERT INTO promo_codes "  # noqa: S608
                    "(code, audience, discount_kind, discount_value, discounts_by_plan, max_redemptions, "
                    "max_redemptions_per_user, redemptions_count, expires_at, is_active, note, created_by) "
                    "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, $12) "
                    f"RETURNING {_PROMO_SELECT_COLUMNS}",
                    promo.code,
                    promo.audience.value,
                    promo.discount_kind.value,
                    discount_value_db,
                    _encode_discounts_by_plan(promo.discounts_by_plan),
                    promo.max_redemptions,
                    promo.max_redemptions_per_user,
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
        # discounts_by_plan сериализуется в JSON и кастуется к ::jsonb (кодек не зарегистрирован).
        params: list[Any] = [code]
        assignments: list[str] = []
        for col, value in safe.items():
            param_value: Any = value
            suffix = ""
            if col == "discounts_by_plan":
                param_value = _encode_discounts_by_plan(value if isinstance(value, dict) else None)
                suffix = "::jsonb"
            assignments.append(f"{col} = ${len(params) + 1}{suffix}")
            params.append(param_value)
        assignments.append("updated_at = now()")
        sql = (
            "UPDATE promo_codes SET "  # noqa: S608 -- белый список _UPDATABLE_COLUMNS, значения параметризованы ($N)
            + ", ".join(assignments)
            + f" WHERE code = $1 RETURNING {_PROMO_SELECT_COLUMNS}"
        )
        async with _connection_scope(self._pool, self._connection) as conn:
            row = await conn.fetchrow(sql, *params)
        return None if row is None else record_from_row(row)

    async def delete(self, code: str) -> bool:
        async with _connection_scope(self._pool, self._connection) as conn:
            result = await conn.execute("DELETE FROM promo_codes WHERE code = $1", code)
        return "DELETE 1" in result

    async def list_codes(
        self, *, limit: int = 100, offset: int = 0, include_inactive: bool = True
    ) -> tuple[PromoCode, ...]:
        async with _connection_scope(self._pool, self._connection) as conn:
            if include_inactive:
                rows = await conn.fetch(
                    f"SELECT {_PROMO_SELECT_COLUMNS} "  # noqa: S608
                    "FROM promo_codes ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                    limit,
                    offset,
                )
            else:
                rows = await conn.fetch(
                    f"SELECT {_PROMO_SELECT_COLUMNS} "  # noqa: S608
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
    def __init__(self, pool: asyncpg.Pool, *, connection: asyncpg.Connection | None = None) -> None:
        self._pool = pool
        self._connection = connection

    async def list_by_user(self, internal_user_id: str, limit: int = 20) -> tuple[PromoRedemptionRecord, ...]:
        async with _connection_scope(self._pool, self._connection) as conn:
            rows = await conn.fetch(
                "SELECT redemption_id, code, internal_user_id, idempotency_key, "
                "base_rubles, discounted_rubles, discount_kopecks, payment_ref, created_at "
                "FROM promo_redemptions WHERE internal_user_id = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                internal_user_id,
                limit,
            )
        return tuple(_redemption_from_row(r) for r in rows)

    async def count_for_user(self, code: str, internal_user_id: str) -> int:
        """Сколько раз пользователь уже погашал этот код (применение/оплата/бейдж)."""
        async with _connection_scope(self._pool, self._connection) as conn:
            return await self.count_for_user_in_connection(conn, code, internal_user_id)

    @staticmethod
    async def count_for_user_in_connection(conn: asyncpg.Connection, code: str, internal_user_id: str) -> int:
        """Транзакционный аналог count_for_user (внутри advisory-лока погашения)."""
        value = await conn.fetchval(
            "SELECT COUNT(*) FROM promo_redemptions WHERE code = $1 AND internal_user_id = $2",
            code,
            internal_user_id,
        )
        return int(value or 0)

    @staticmethod
    async def redeem_in_connection(conn: asyncpg.Connection, record: PromoRedemptionRecord) -> bool:
        """Идемпотентная вставка погашения (True — вставлено, False — дубль по ключу).

        Конфликт-таргет — только idempotency_key (повтор вебхука/двойной клик).
        Per-user лимит (максимум погашений кода одним пользователем) инфорсируется
        выше по стеку — count-гейтом под advisory-локом в _redeem_atomic.
        """
        result = await conn.execute(
            "INSERT INTO promo_redemptions "
            "(redemption_id, code, internal_user_id, idempotency_key, "
            "base_rubles, discounted_rubles, discount_kopecks, payment_ref, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
            "ON CONFLICT (idempotency_key) DO NOTHING",
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
    def __init__(self, pool: asyncpg.Pool, *, connection: asyncpg.Connection | None = None) -> None:
        self._pool = pool
        self._connection = connection

    async def get(self, internal_user_id: str) -> PromoAppliedRecord | None:
        async with _connection_scope(self._pool, self._connection) as conn:
            row = await conn.fetchrow(
                "SELECT internal_user_id, code, applied_at, expires_at FROM promo_applied WHERE internal_user_id = $1",
                internal_user_id,
            )
        return None if row is None else _applied_from_row(row)

    async def upsert(self, internal_user_id: str, code: str, expires_at: datetime) -> PromoAppliedRecord:
        async with _connection_scope(self._pool, self._connection) as conn:
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
        async with _connection_scope(self._pool, self._connection) as conn:
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
