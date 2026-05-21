"""PostgreSQL referral persistence: codes, relationships, balances, transactions."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

import asyncpg

from app.persistence.referral_contracts import (
    ReferralBalanceRecord,
    ReferralCodeRecord,
    ReferralRelationshipRecord,
    ReferralTransactionRecord,
)

_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_CODE_LENGTH = 8


def _generate_referral_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


class PostgresReferralCodeRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_or_create(self, internal_user_id: str) -> ReferralCodeRecord:
        async with self._pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT internal_user_id, referral_code, created_at FROM referral_codes WHERE internal_user_id = $1",
                internal_user_id,
            )
            if existing is not None:
                return ReferralCodeRecord(
                    internal_user_id=existing["internal_user_id"],
                    referral_code=existing["referral_code"],
                    created_at=existing["created_at"],
                )
            code = _generate_referral_code()
            now = datetime.now(UTC)
            row = await conn.fetchrow(
                """
                INSERT INTO referral_codes (internal_user_id, referral_code, created_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (internal_user_id) DO UPDATE SET internal_user_id = EXCLUDED.internal_user_id
                RETURNING internal_user_id, referral_code, created_at
                """,
                internal_user_id,
                code,
                now,
            )
            return ReferralCodeRecord(
                internal_user_id=row["internal_user_id"],
                referral_code=row["referral_code"],
                created_at=row["created_at"],
            )

    async def find_by_code(self, referral_code: str) -> ReferralCodeRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT internal_user_id, referral_code, created_at FROM referral_codes WHERE referral_code = $1",
                referral_code,
            )
            if row is None:
                return None
            return ReferralCodeRecord(
                internal_user_id=row["internal_user_id"],
                referral_code=row["referral_code"],
                created_at=row["created_at"],
            )


class PostgresReferralRelationshipRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_relationship(
        self,
        *,
        referred_user_id: str,
        referrer_user_id: str,
        level: int,
        referrer_of_referrer_user_id: str | None,
    ) -> ReferralRelationshipRecord:
        rid = str(uuid.uuid4())
        now = datetime.now(UTC)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO referral_relationships (relationship_id, referred_user_id, referrer_user_id, level, referrer_of_referrer_user_id, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                rid,
                referred_user_id,
                referrer_user_id,
                level,
                referrer_of_referrer_user_id,
                now,
            )
        return ReferralRelationshipRecord(
            relationship_id=rid,
            referred_user_id=referred_user_id,
            referrer_user_id=referrer_user_id,
            level=level,
            referrer_of_referrer_user_id=referrer_of_referrer_user_id,
            created_at=now,
        )

    async def find_referrers(self, user_id: str) -> tuple[ReferralRelationshipRecord, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT relationship_id, referred_user_id, referrer_user_id, level, referrer_of_referrer_user_id, created_at "
                "FROM referral_relationships WHERE referred_user_id = $1",
                user_id,
            )
        return tuple(
            ReferralRelationshipRecord(
                relationship_id=r["relationship_id"],
                referred_user_id=r["referred_user_id"],
                referrer_user_id=r["referrer_user_id"],
                level=r["level"],
                referrer_of_referrer_user_id=r["referrer_of_referrer_user_id"],
                created_at=r["created_at"],
            )
            for r in rows
        )


class PostgresReferralBalanceRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_balance(self, internal_user_id: str) -> ReferralBalanceRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT internal_user_id, balance_kopecks, updated_at FROM referral_balances WHERE internal_user_id = $1",
                internal_user_id,
            )
        if row is None:
            return None
        return ReferralBalanceRecord(
            internal_user_id=row["internal_user_id"],
            balance_kopecks=row["balance_kopecks"],
            updated_at=row["updated_at"],
        )

    async def credit(self, internal_user_id: str, amount_kopecks: int) -> ReferralBalanceRecord:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO referral_balances (internal_user_id, balance_kopecks, updated_at) "
                "VALUES ($1, $2, now()) "
                "ON CONFLICT (internal_user_id) DO UPDATE SET balance_kopecks = referral_balances.balance_kopecks + $2, updated_at = now() "
                "RETURNING internal_user_id, balance_kopecks, updated_at",
                internal_user_id,
                amount_kopecks,
            )
        return ReferralBalanceRecord(
            internal_user_id=row["internal_user_id"],
            balance_kopecks=row["balance_kopecks"],
            updated_at=row["updated_at"],
        )

    async def debit(self, internal_user_id: str, amount_kopecks: int) -> ReferralBalanceRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE referral_balances SET balance_kopecks = balance_kopecks - $2, updated_at = now() "
                "WHERE internal_user_id = $1 AND balance_kopecks >= $2 "
                "RETURNING internal_user_id, balance_kopecks, updated_at",
                internal_user_id,
                amount_kopecks,
            )
        if row is None:
            return None
        return ReferralBalanceRecord(
            internal_user_id=row["internal_user_id"],
            balance_kopecks=row["balance_kopecks"],
            updated_at=row["updated_at"],
        )


class PostgresReferralTransactionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append_transaction(self, record: ReferralTransactionRecord) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO referral_transactions "
                "(transaction_id, internal_user_id, amount_kopecks, transaction_type, related_user_id, related_plan_id, description, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                record.transaction_id,
                record.internal_user_id,
                record.amount_kopecks,
                record.transaction_type,
                record.related_user_id,
                record.related_plan_id,
                record.description,
                record.created_at,
            )

    async def append_transaction_if_description_absent(self, record: ReferralTransactionRecord) -> bool:
        """Atomic dedup: insert only if no row with same (internal_user_id, description) exists. Returns True if inserted."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "INSERT INTO referral_transactions "
                "(transaction_id, internal_user_id, amount_kopecks, transaction_type, related_user_id, related_plan_id, description, created_at) "
                "SELECT $1, $2, $3, $4, $5, $6, $7, $8 "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM referral_transactions WHERE internal_user_id = $2 AND description = $7"
                ")",
                record.transaction_id,
                record.internal_user_id,
                record.amount_kopecks,
                record.transaction_type,
                record.related_user_id,
                record.related_plan_id,
                record.description,
                record.created_at,
            )
            return "INSERT 0 1" in result

    async def list_by_user(self, internal_user_id: str, limit: int = 20) -> tuple[ReferralTransactionRecord, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT transaction_id, internal_user_id, amount_kopecks, transaction_type, "
                "related_user_id, related_plan_id, description, created_at "
                "FROM referral_transactions WHERE internal_user_id = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                internal_user_id,
                limit,
            )
        return tuple(
            ReferralTransactionRecord(
                transaction_id=r["transaction_id"],
                internal_user_id=r["internal_user_id"],
                amount_kopecks=r["amount_kopecks"],
                transaction_type=r["transaction_type"],
                related_user_id=r["related_user_id"],
                related_plan_id=r["related_plan_id"],
                description=r["description"],
                created_at=r["created_at"],
            )
            for r in rows
        )
