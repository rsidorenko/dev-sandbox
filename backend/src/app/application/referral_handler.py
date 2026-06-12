"""Referral handler: get/create referral code, get balance, list transactions."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.referral import rubles_from_kopecks
from app.persistence.referral_contracts import (
    ReferralBalanceRepository,
    ReferralCodeRepository,
    ReferralRelationshipRepository,
)


@dataclass(frozen=True, slots=True)
class ReferralInfo:
    referral_code: str
    referral_link: str
    web_referral_link: str
    balance_rubles: float
    direct_referrals_count: int


@dataclass(frozen=True, slots=True)
class ReferralBalanceInfo:
    balance_rubles: float
    balance_kopecks: int


async def apply_referral_on_registration(
    *,
    new_internal_user_id: str,
    referral_code: str,
    code_repo: ReferralCodeRepository,
    relationship_repo: ReferralRelationshipRepository,
) -> None:
    """Create L1/L2 referral relationships on first registration via referral code.

    Shared by both Telegram bot bootstrap and web email verification.
    Guards: invalid code, self-referral, duplicate relationships.
    """
    referrer_record = await code_repo.find_by_code(referral_code)
    if referrer_record is None:
        return
    if referrer_record.internal_user_id == new_internal_user_id:
        return
    existing = await relationship_repo.find_referrers(new_internal_user_id)
    if existing:
        return

    referrer_of_referrer_id: str | None = None
    referrer_referrers = await relationship_repo.find_referrers(
        referrer_record.internal_user_id,
    )
    for rr in referrer_referrers:
        if rr.level == 1:
            referrer_of_referrer_id = rr.referrer_user_id
            break

    await relationship_repo.create_relationship(
        referred_user_id=new_internal_user_id,
        referrer_user_id=referrer_record.internal_user_id,
        level=1,
        referrer_of_referrer_user_id=None,
    )
    if referrer_of_referrer_id is not None and referrer_of_referrer_id != new_internal_user_id:
        await relationship_repo.create_relationship(
            referred_user_id=new_internal_user_id,
            referrer_user_id=referrer_of_referrer_id,
            level=2,
            referrer_of_referrer_user_id=referrer_record.internal_user_id,
        )


async def get_or_create_referral_code(
    *,
    internal_user_id: str,
    code_repo: ReferralCodeRepository,
    bot_username: str,
    site_base_url: str,
) -> tuple[str, str]:
    """Get or create referral code, return (telegram_link, web_link)."""
    record = await code_repo.get_or_create(internal_user_id)
    tg_link = f"https://t.me/{bot_username}?start=ref_{record.referral_code}"
    web_link = f"{site_base_url}/?ref={record.referral_code}"
    return tg_link, web_link


async def get_referral_info(
    *,
    internal_user_id: str,
    code_repo: ReferralCodeRepository,
    balance_repo: ReferralBalanceRepository,
    relationship_repo: ReferralRelationshipRepository,
    bot_username: str,
    site_base_url: str,
) -> ReferralInfo:
    code_record = await code_repo.get_or_create(internal_user_id)
    tg_link = f"https://t.me/{bot_username}?start=ref_{code_record.referral_code}"
    web_link = f"{site_base_url}/?ref={code_record.referral_code}"
    balance_record = await balance_repo.get_balance(internal_user_id)
    balance_kopecks = balance_record.balance_kopecks if balance_record else 0
    relationships = await relationship_repo.find_referrers(internal_user_id)
    direct_count = sum(1 for r in relationships if r.level == 1)
    return ReferralInfo(
        referral_code=code_record.referral_code,
        referral_link=tg_link,
        web_referral_link=web_link,
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
