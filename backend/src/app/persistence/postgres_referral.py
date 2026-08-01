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
            now = datetime.now(UTC)
            # ON CONFLICT covers the internal_user_id PK only; a referral_code UNIQUE
            # collision would raise UniqueViolationError (no inference for that column).
            # Regenerate the code and retry — vanishingly rare with the 8-char alphabet
            # but must not surface as a 500 to the dashboard.
            row = None
            for _ in range(5):
                code = _generate_referral_code()
                try:
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
                    break
                except asyncpg.UniqueViolationError:
                    continue
            if row is None:
                # All attempts collided on referral_code (effectively impossible) — fall
                # back to whatever row now exists for this internal_user_id.
                row = await conn.fetchrow(
                    "SELECT internal_user_id, referral_code, created_at FROM referral_codes WHERE internal_user_id = $1",
                    internal_user_id,
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
            return await self.find_referrers_in_connection(conn, user_id)

    async def count_direct_referrals(self, user_id: str) -> int:
        """Number of users this user directly invited (L1 relationships where they
        are the referrer). Distinct from find_referrers (which returns who referred
        *this* user) — this powers the 'invited' count shown in the bot/web."""
        async with self._pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM referral_relationships WHERE referrer_user_id = $1 AND level = 1",
                    user_id,
                )
            )

    @staticmethod
    async def find_referrers_in_connection(
        conn: asyncpg.Connection,
        user_id: str,
    ) -> tuple[ReferralRelationshipRecord, ...]:
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
            return await self.get_balance_in_connection(conn, internal_user_id)

    @staticmethod
    async def get_balance_in_connection(
        conn: asyncpg.Connection,
        internal_user_id: str,
    ) -> ReferralBalanceRecord | None:
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
            return await self.credit_in_connection(conn, internal_user_id, amount_kopecks)

    @staticmethod
    async def credit_in_connection(
        conn: asyncpg.Connection,
        internal_user_id: str,
        amount_kopecks: int,
    ) -> ReferralBalanceRecord:
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
            return await self.debit_in_connection(conn, internal_user_id, amount_kopecks)

    @staticmethod
    async def debit_in_connection(
        conn: asyncpg.Connection,
        internal_user_id: str,
        amount_kopecks: int,
    ) -> ReferralBalanceRecord | None:
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
                "(transaction_id, internal_user_id, amount_kopecks, transaction_type, related_user_id, related_plan_id, description, idempotency_key, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                record.transaction_id,
                record.internal_user_id,
                record.amount_kopecks,
                record.transaction_type,
                record.related_user_id,
                record.related_plan_id,
                record.description,
                record.idempotency_key,
                record.created_at,
            )

    async def append_transaction_if_idempotency_absent(self, record: ReferralTransactionRecord) -> bool:
        """Atomic dedup: insert only if no row with same (internal_user_id, idempotency_key)
        exists. Returns True if inserted. Dedup moved off ``description`` (now a clean,
        repeatable label) onto the dedicated ``idempotency_key`` — see migration 070."""
        async with self._pool.acquire() as conn:
            return await self.append_transaction_if_idempotency_absent_in_connection(conn, record)

    @staticmethod
    async def append_transaction_if_idempotency_absent_in_connection(
        conn: asyncpg.Connection,
        record: ReferralTransactionRecord,
    ) -> bool:
        result = await conn.execute(
            "INSERT INTO referral_transactions "
            "(transaction_id, internal_user_id, amount_kopecks, transaction_type, related_user_id, related_plan_id, description, idempotency_key, created_at) "
            "SELECT $1, $2, $3, $4, $5, $6, $7, $8, $9 "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM referral_transactions WHERE internal_user_id = $2 AND idempotency_key = $8"
            ")",
            record.transaction_id,
            record.internal_user_id,
            record.amount_kopecks,
            record.transaction_type,
            record.related_user_id,
            record.related_plan_id,
            record.description,
            record.idempotency_key,
            record.created_at,
        )
        return "INSERT 0 1" in result

    async def list_by_user(self, internal_user_id: str, limit: int = 20) -> tuple[ReferralTransactionRecord, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT transaction_id, internal_user_id, amount_kopecks, transaction_type, "
                "related_user_id, related_plan_id, description, idempotency_key, created_at "
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
                idempotency_key=r["idempotency_key"],
                created_at=r["created_at"],
            )
            for r in rows
        )
