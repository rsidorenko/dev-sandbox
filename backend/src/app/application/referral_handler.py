"""Referral handler: get/create referral code, get balance, list transactions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.referral import rubles_from_kopecks
from app.persistence.referral_contracts import (
    ReferralBalanceRecord,
    ReferralCodeRepository,
    ReferralRelationshipRepository,
    ReferralTransactionRepository,
)


@dataclass(frozen=True, slots=True)
class ReferralInfo:
    referral_code: str
    referral_link: str
    balance_rubles: float
    direct_referrals_count: int


@dataclass(frozen=True, slots=True)
class ReferralBalanceInfo:
    balance_rubles: float
    balance_kopecks: int


async def get_or_create_referral_code(
    *,
    internal_user_id: str,
    code_repo: ReferralCodeRepository,
    bot_username: str,
) -> str:
    record = await code_repo.get_or_create(internal_user_id)
    return f"https://t.me/{bot_username}?start=ref_{record.referral_code}"


async def get_referral_info(
    *,
    internal_user_id: str,
    code_repo: ReferralCodeRepository,
    balance_repo: ReferralBalanceRepository,
    relationship_repo: ReferralRelationshipRepository,
    bot_username: str,
) -> ReferralInfo:
    code_record = await code_repo.get_or_create(internal_user_id)
    link = f"https://t.me/{bot_username}?start=ref_{code_record.referral_code}"
    balance_record = await balance_repo.get_balance(internal_user_id)
    balance_kopecks = balance_record.balance_kopecks if balance_record else 0
    relationships = await relationship_repo.find_referrers(internal_user_id)
    direct_count = sum(1 for r in relationships if r.level == 1)
    return ReferralInfo(
        referral_code=code_record.referral_code,
        referral_link=link,
        balance_rubles=rubles_from_kopecks(balance_kopecks),
        direct_referrals_count=direct_count,
    )


async def get_referral_balance(
    *,
    internal_user_id: str,
    balance_repo: ReferralBalanceRepository,
) -> ReferralBalanceInfo:
    record = await balance_repo.get_balance(internal_user_id)
    kopecks = record.balance_kopecks if record else 0
    return ReferralBalanceInfo(
        balance_rubles=rubles_from_kopecks(kopecks),
        balance_kopecks=kopecks,
    )
