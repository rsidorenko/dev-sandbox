"""PostgreSQL promo code persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg

from app.domain.promo import PromoCode

# Column list inlined into each query (no f-strings) — matches postgres_referral style
# and avoids ruff S608 false positives on string-based SQL.


def _row_to_promo(row: asyncpg.Record | None) -> PromoCode | None:
    if row is None:
        return None
    return PromoCode(
        code=row["code"],
        owner_internal_user_id=row["owner_internal_user_id"],
        discount_percent=row["discount_percent"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        max_uses=row["max_uses"],
        uses_count=row["uses_count"],
        active=row["active"],
    )


class PostgresPromoCodeRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_by_code(self, code: str) -> PromoCode | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT code, owner_internal_user_id, discount_percent, valid_from, valid_until, "
                "max_uses, uses_count, active FROM promo_codes WHERE code = $1",
                code,
            )
        return _row_to_promo(row)

    async def create(self, record: PromoCode) -> PromoCode:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO promo_codes
                   (code, owner_internal_user_id, discount_percent, valid_from, valid_until, max_uses, uses_count, active)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code
                   RETURNING code, owner_internal_user_id, discount_percent, valid_from, valid_until,
                             max_uses, uses_count, active""",
                record.code,
                record.owner_internal_user_id,
                record.discount_percent,
                record.valid_from,
                record.valid_until,
                record.max_uses,
                record.uses_count,
                record.active,
            )
        return _row_to_promo(row)  # type: ignore[return-value]

    async def consume(self, code: str) -> bool:
        """Atomically spend one use of a currently-usable code. Returns True if spent."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE promo_codes
                   SET uses_count = uses_count + 1
                   WHERE code = $1
                     AND active = TRUE
                     AND uses_count < max_uses
                     AND valid_from <= NOW()
                     AND (valid_until IS NULL OR valid_until > NOW())""",
                code,
            )
        return "UPDATE 1" in result

    async def ensure_personal_code(
        self,
        *,
        owner_internal_user_id: str,
        code: str,
        discount_percent: int,
        valid_days: int,
        max_uses: int = 1,
    ) -> PromoCode:
        """Idempotently create a personal promo for an owner. Returns the record (existing or new)."""
        async with self._pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT code, owner_internal_user_id, discount_percent, valid_from, valid_until, "
                "max_uses, uses_count, active FROM promo_codes WHERE code = $1",
                code,
            )
            if existing is not None:
                return _row_to_promo(existing)  # type: ignore[return-value]
            now = datetime.now(UTC)
            valid_until = now + timedelta(days=valid_days)
            await conn.execute(
                """INSERT INTO promo_codes
                   (code, owner_internal_user_id, discount_percent, valid_from, valid_until, max_uses, uses_count, active)
                   VALUES ($1, $2, $3, $4, $5, $6, 0, TRUE)
                   ON CONFLICT (code) DO NOTHING""",
                code,
                owner_internal_user_id,
                discount_percent,
                now,
                valid_until,
                max_uses,
            )
            row = await conn.fetchrow(
                "SELECT code, owner_internal_user_id, discount_percent, valid_from, valid_until, "
                "max_uses, uses_count, active FROM promo_codes WHERE code = $1",
                code,
            )
        return _row_to_promo(row)  # type: ignore[return-value]
