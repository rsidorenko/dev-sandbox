"""Referral persistence contracts (protocols, no concrete DB)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ReferralCodeRecord:
    internal_user_id: str
    referral_code: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReferralRelationshipRecord:
    relationship_id: str
    referred_user_id: str
    referrer_user_id: str
    level: int
    referrer_of_referrer_user_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReferralBalanceRecord:
    internal_user_id: str
    balance_kopecks: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReferralTransactionRecord:
    transaction_id: str
    internal_user_id: str
    amount_kopecks: int
    transaction_type: str
    related_user_id: str | None
    related_plan_id: str | None
    description: str | None
    created_at: datetime


class ReferralCodeRepository(Protocol):
    async def get_or_create(self, internal_user_id: str) -> ReferralCodeRecord: ...
    async def find_by_code(self, referral_code: str) -> ReferralCodeRecord | None: ...


class ReferralRelationshipRepository(Protocol):
    async def create_relationship(
        self,
        *,
        referred_user_id: str,
        referrer_user_id: str,
        level: int,
        referrer_of_referrer_user_id: str | None,
    ) -> ReferralRelationshipRecord: ...
    async def find_referrers(self, user_id: str) -> tuple[ReferralRelationshipRecord, ...]: ...


class ReferralBalanceRepository(Protocol):
    async def get_balance(self, internal_user_id: str) -> ReferralBalanceRecord | None: ...
    async def credit(self, internal_user_id: str, amount_kopecks: int) -> ReferralBalanceRecord: ...
    async def debit(self, internal_user_id: str, amount_kopecks: int) -> ReferralBalanceRecord | None: ...


class ReferralTransactionRepository(Protocol):
    async def append_transaction(self, record: ReferralTransactionRecord) -> None: ...
    async def append_transaction_if_description_absent(self, record: ReferralTransactionRecord) -> bool: ...
    async def list_by_user(self, internal_user_id: str, limit: int = 20) -> tuple[ReferralTransactionRecord, ...]: ...
