"""Tests for domain.referral: commission rates and calculations."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.application.referral_handler import apply_referral_on_registration, get_referral_info
from app.domain.referral import (
    BALANCED_MAX_REWARD_RATE,
    BALANCED_V1_POLICY,
    LEGACY_STACK_POLICY,
    REFERRAL_BONUS_DAYS,
    REFERRAL_BONUS_LEVEL,
    REFERRAL_BONUS_QUALIFYING_PLANS,
    REFERRAL_INVITEE_BONUS_DAYS,
    ReferralCommission,
    balanced_referral_economics,
    build_commissions_for_payment,
    calculate_commission_kopecks,
    is_qualifying_plan_for_bonus,
    level1_commission_rate,
    level2_commission_rate,
    resolve_direct_and_indirect_referrers,
    resolve_reward_policy,
    rubles_from_kopecks,
)
from app.persistence.in_memory import (
    InMemoryReferralBalanceRepository,
    InMemoryReferralBonusAwardRepository,
    InMemoryReferralCodeRepository,
    InMemoryReferralInviteeBonusAwardRepository,
    InMemoryReferralRelationshipRepository,
    InMemoryReferralTransactionRepository,
)
from app.persistence.referral_contracts import (
    ReferralBonusAwardRecord,
    ReferralInviteeBonusAwardRecord,
    ReferralTransactionRecord,
)
from app.shared.test_helpers import run_async as _run


def test_level1_rates():
    assert level1_commission_rate("1m") == 0.35
    assert level1_commission_rate("3m") == 0.30
    assert level1_commission_rate("6m") == 0.20
    assert level1_commission_rate("365d") == 0.17


def test_level2_rates():
    assert level2_commission_rate("1m") == 0.05
    assert level2_commission_rate("3m") == 0.03
    assert level2_commission_rate("6m") == 0.02


def test_balanced_rates_are_lower_and_capped() -> None:
    assert level1_commission_rate("1m", BALANCED_V1_POLICY) == 0.20
    assert level2_commission_rate("1m", BALANCED_V1_POLICY) == 0.02
    assert level1_commission_rate("365d", BALANCED_V1_POLICY) == 0.15
    assert level2_commission_rate("365d", BALANCED_V1_POLICY) == 0.01


def test_balanced_economics_never_exceeds_cap() -> None:
    qualifying = ("1m", "3m", "6m", "365d")
    economics = [balanced_referral_economics(plan_id) for plan_id in qualifying]
    assert max(item.total_reward_rate for item in economics) <= BALANCED_MAX_REWARD_RATE
    assert balanced_referral_economics("1m").total_reward_rate == pytest.approx(0.32)


def test_unknown_plan_rate():
    assert level1_commission_rate("99m") == 0.0
    assert level2_commission_rate("99m") == 0.0


def test_calculate_commission_kopecks():
    assert calculate_commission_kopecks(24900, 0.35) == 8715  # 249 RUB (1m plan) * 35% = 87.15 RUB


def test_build_commissions_direct_only():
    result = build_commissions_for_payment(
        payer_user_id="user_b",
        direct_referrer_user_id="user_a",
        indirect_referrer_user_id=None,
        plan_id="1m",
        payment_amount_kopecks=30000,
    )
    assert len(result) == 1
    assert result[0].level == 1
    assert result[0].amount_kopecks == 10500  # 300 * 0.35
    assert result[0].referrer_user_id == "user_a"
    assert result[0].payer_user_id == "user_b"


def test_build_commissions_both_levels():
    result = build_commissions_for_payment(
        payer_user_id="user_c",
        direct_referrer_user_id="user_b",
        indirect_referrer_user_id="user_a",
        plan_id="3m",
        payment_amount_kopecks=75000,
    )
    assert len(result) == 2
    l1 = next(c for c in result if c.level == 1)
    l2 = next(c for c in result if c.level == 2)
    assert l1.amount_kopecks == 75000 * 30 // 100  # 22500
    assert l2.amount_kopecks == 75000 * 3 // 100  # 2250


def test_build_commissions_no_referrer():
    result = build_commissions_for_payment(
        payer_user_id="user_a",
        direct_referrer_user_id=None,
        indirect_referrer_user_id=None,
        plan_id="1m",
        payment_amount_kopecks=30000,
    )
    assert len(result) == 0


def test_rubles_from_kopecks():
    assert rubles_from_kopecks(30000) == 300.0
    assert rubles_from_kopecks(0) == 0.0
    assert rubles_from_kopecks(10500) == 105.0


def test_six_months_commissions():
    result = build_commissions_for_payment(
        payer_user_id="user_c",
        direct_referrer_user_id="user_b",
        indirect_referrer_user_id="user_a",
        plan_id="6m",
        payment_amount_kopecks=135000,
    )
    assert len(result) == 2
    l1 = next(c for c in result if c.level == 1)
    l2 = next(c for c in result if c.level == 2)
    assert l1.amount_kopecks == 135000 * 20 // 100  # 27000
    assert l2.amount_kopecks == 135000 * 2 // 100  # 2700


def test_resolve_referrers_empty():
    direct, indirect = resolve_direct_and_indirect_referrers(())
    assert direct is None
    assert indirect is None


def test_resolve_referrers_direct_only():
    rels = (ReferralCommission("a", 100, 1, "1m", "b"),)
    direct, indirect = resolve_direct_and_indirect_referrers(rels)
    assert direct == "a"
    assert indirect is None


def test_resolve_referrers_both_levels():
    rels = (
        ReferralCommission("a", 100, 1, "1m", "c"),
        ReferralCommission("b", 50, 2, "1m", "c"),
    )
    direct, indirect = resolve_direct_and_indirect_referrers(rels)
    assert direct == "a"
    assert indirect == "b"


def test_reward_policy_comes_from_direct_relationship() -> None:
    legacy = ReferralCommission("a", 100, 1, "1m", "b")
    assert resolve_reward_policy((legacy,)) == LEGACY_STACK_POLICY
    balanced = SimpleNamespace(level=1, reward_policy=BALANCED_V1_POLICY)
    assert resolve_reward_policy((balanced,)) == BALANCED_V1_POLICY


def test_calculate_commission_zero_rate():
    assert calculate_commission_kopecks(30000, 0.0) == 0


# --- Tests for apply_referral_on_registration ---


def test_apply_referral_creates_l1_relationship() -> None:
    async def main() -> None:
        code_repo = InMemoryReferralCodeRepository()
        rel_repo = InMemoryReferralRelationshipRepository()

        # Create referrer with a code
        referrer_record = await code_repo.get_or_create("user_referrer")
        assert referrer_record.referral_code

        # Apply referral for new user
        await apply_referral_on_registration(
            new_internal_user_id="user_new",
            referral_code=referrer_record.referral_code,
            code_repo=code_repo,
            relationship_repo=rel_repo,
        )

        # Verify L1 relationship created
        rels = await rel_repo.find_referrers("user_new")
        assert len(rels) == 1
        assert rels[0].level == 1
        assert rels[0].referrer_user_id == "user_referrer"
        assert rels[0].reward_policy == BALANCED_V1_POLICY

    _run(main())


def test_apply_referral_creates_l2_when_referrer_has_own_referrer() -> None:
    async def main() -> None:
        code_repo = InMemoryReferralCodeRepository()
        rel_repo = InMemoryReferralRelationshipRepository()

        # Create grand-referrer → referrer → new user chain
        await code_repo.get_or_create("user_grand")
        # Manually create L1 for referrer (simulating they were referred by grand)
        await rel_repo.create_relationship(
            referred_user_id="user_referrer",
            referrer_user_id="user_grand",
            level=1,
            referrer_of_referrer_user_id=None,
        )

        # Create referrer's code
        referrer = await code_repo.get_or_create("user_referrer")

        # Apply referral
        await apply_referral_on_registration(
            new_internal_user_id="user_new",
            referral_code=referrer.referral_code,
            code_repo=code_repo,
            relationship_repo=rel_repo,
        )

        rels = await rel_repo.find_referrers("user_new")
        assert len(rels) == 2
        l1 = next(r for r in rels if r.level == 1)
        l2 = next(r for r in rels if r.level == 2)
        assert l1.referrer_user_id == "user_referrer"
        assert l2.referrer_user_id == "user_grand"
        assert l1.reward_policy == BALANCED_V1_POLICY
        assert l2.reward_policy == BALANCED_V1_POLICY

    _run(main())


def test_apply_referral_ignores_invalid_code() -> None:
    async def main() -> None:
        code_repo = InMemoryReferralCodeRepository()
        rel_repo = InMemoryReferralRelationshipRepository()

        # No code exists for "nonexistent" → should silently do nothing
        await apply_referral_on_registration(
            new_internal_user_id="user_new",
            referral_code="nonexistent",
            code_repo=code_repo,
            relationship_repo=rel_repo,
        )

        rels = await rel_repo.find_referrers("user_new")
        assert len(rels) == 0

    _run(main())


def test_apply_referral_prevents_self_referral() -> None:
    async def main() -> None:
        code_repo = InMemoryReferralCodeRepository()
        rel_repo = InMemoryReferralRelationshipRepository()

        # User tries to use their own code
        record = await code_repo.get_or_create("user_a")

        await apply_referral_on_registration(
            new_internal_user_id="user_a",
            referral_code=record.referral_code,
            code_repo=code_repo,
            relationship_repo=rel_repo,
        )

        rels = await rel_repo.find_referrers("user_a")
        assert len(rels) == 0

    _run(main())


def test_apply_referral_skips_if_user_already_has_referrer() -> None:
    async def main() -> None:
        code_repo = InMemoryReferralCodeRepository()
        rel_repo = InMemoryReferralRelationshipRepository()

        # User already has a referrer
        await rel_repo.create_relationship(
            referred_user_id="user_new",
            referrer_user_id="user_old_referrer",
            level=1,
            referrer_of_referrer_user_id=None,
        )

        referrer = await code_repo.get_or_create("user_referrer")

        await apply_referral_on_registration(
            new_internal_user_id="user_new",
            referral_code=referrer.referral_code,
            code_repo=code_repo,
            relationship_repo=rel_repo,
        )

        # Should NOT add another referrer
        rels = await rel_repo.find_referrers("user_new")
        assert len(rels) == 1
        assert rels[0].referrer_user_id == "user_old_referrer"

    _run(main())


# --- Tests for count_direct_referrals / get_referral_info direct count ---
# Regression: direct_referrals_count must be the number of users THIS user invited
# (referrer_user_id = them). The old code used find_referrers (referred_user_id =
# them = their referrers), so referrers showed 0 and referred users showed 1.


def test_count_direct_referrals_counts_users_they_invited() -> None:
    async def main() -> None:
        rel_repo = InMemoryReferralRelationshipRepository()
        # referrer invited two users (L1)
        for referred in ("user_invited_a", "user_invited_b"):
            await rel_repo.create_relationship(
                referred_user_id=referred,
                referrer_user_id="user_referrer",
                level=1,
                referrer_of_referrer_user_id=None,
            )
        # an L2 relationship (grand-referrer) — must NOT count as a direct referral
        await rel_repo.create_relationship(
            referred_user_id="user_invited_a",
            referrer_user_id="user_grand",
            level=2,
            referrer_of_referrer_user_id="user_referrer",
        )

        assert await rel_repo.count_direct_referrals("user_referrer") == 2
        # A user who was invited but invited nobody -> 0 (old bug returned 1 here)
        assert await rel_repo.count_direct_referrals("user_invited_a") == 0
        # L2 relationships don't count as direct
        assert await rel_repo.count_direct_referrals("user_grand") == 0

    _run(main())


def test_get_referral_info_direct_count_is_invited_not_referrers() -> None:
    async def main() -> None:
        code_repo = InMemoryReferralCodeRepository()
        rel_repo = InMemoryReferralRelationshipRepository()
        bal_repo = InMemoryReferralBalanceRepository()
        tx_repo = InMemoryReferralTransactionRepository()

        # referrer invited two users
        await code_repo.get_or_create("user_referrer")
        for referred in ("user_invited_a", "user_invited_b"):
            await rel_repo.create_relationship(
                referred_user_id=referred,
                referrer_user_id="user_referrer",
                level=1,
                referrer_of_referrer_user_id=None,
            )

        info = await get_referral_info(
            internal_user_id="user_referrer",
            code_repo=code_repo,
            balance_repo=bal_repo,
            relationship_repo=rel_repo,
            transaction_repo=tx_repo,
            bonus_repo=InMemoryReferralBonusAwardRepository(),
            bot_username="bot",
            site_base_url="https://site.example",
        )
        # The referrer invited 2 — must show 2, not 0 (the old find_referrers-based
        # count returned 0 because the referrer was never referred by anyone).
        assert info.l1_invited == 2
        assert info.l2_invited == 0
        assert info.total_earned_rubles == 0.0

    _run(main())


def test_get_referral_info_l2_invited_and_total_earned() -> None:
    async def main() -> None:
        code_repo = InMemoryReferralCodeRepository()
        rel_repo = InMemoryReferralRelationshipRepository()
        bal_repo = InMemoryReferralBalanceRepository()
        tx_repo = InMemoryReferralTransactionRepository()

        await code_repo.get_or_create("user_referrer")
        await rel_repo.create_relationship(
            referred_user_id="friend_a",
            referrer_user_id="user_referrer",
            level=1,
            referrer_of_referrer_user_id=None,
        )
        await rel_repo.create_relationship(
            referred_user_id="friend_of_friend",
            referrer_user_id="user_referrer",
            level=2,
            referrer_of_referrer_user_id="friend_a",
        )
        # Two referral credits: 87.15 ₽ + 12.00 ₽ = 99.15 ₽ earned total.
        now = datetime.now(UTC)
        for amount, payer in ((8715, "friend_a"), (1200, "friend_of_friend")):
            await tx_repo.append_transaction(
                ReferralTransactionRecord(
                    transaction_id=f"tx-{amount}",
                    internal_user_id="user_referrer",
                    amount_kopecks=amount,
                    transaction_type="referral_credit",
                    related_user_id=payer,
                    related_plan_id="1m",
                    description="x",
                    idempotency_key=f"ik-{amount}",
                    created_at=now,
                )
            )

        info = await get_referral_info(
            internal_user_id="user_referrer",
            code_repo=code_repo,
            balance_repo=bal_repo,
            relationship_repo=rel_repo,
            transaction_repo=tx_repo,
            bonus_repo=InMemoryReferralBonusAwardRepository(),
            bot_username="bot",
            site_base_url="https://site.example",
        )
        assert info.l1_invited == 1
        assert info.l2_invited == 1
        assert info.total_earned_rubles == 99.15

    _run(main())


# --- 'Free month per friend' bonus: qualifying gate + idempotent award ledger ---


def test_bonus_constants() -> None:
    assert REFERRAL_BONUS_DAYS == 30
    assert REFERRAL_BONUS_LEVEL == 1
    assert REFERRAL_BONUS_QUALIFYING_PLANS == frozenset({"1m", "3m", "6m", "365d"})
    assert REFERRAL_INVITEE_BONUS_DAYS == 3


def test_is_qualifying_plan_for_bonus() -> None:
    # Only plans >= 1 month qualify (avoids 1-day/12₽ abuse → 30 free days).
    for plan in ("1m", "3m", "6m", "365d"):
        assert is_qualifying_plan_for_bonus(plan) is True
    for plan in ("1d", "7d", "14d"):
        assert is_qualifying_plan_for_bonus(plan) is False
    # Unknown / custom plans do not qualify.
    assert is_qualifying_plan_for_bonus("custom:30") is False
    assert is_qualifying_plan_for_bonus("nonsense") is False


def _bonus_record(*, referrer: str, payer: str) -> ReferralBonusAwardRecord:
    return ReferralBonusAwardRecord(
        award_id=f"award-{payer}",
        referrer_user_id=referrer,
        payer_user_id=payer,
        level=REFERRAL_BONUS_LEVEL,
        days_awarded=REFERRAL_BONUS_DAYS,
        related_plan_id="1m",
        correlation_id="cid",
        created_at=datetime.now(UTC),
    )


def test_bonus_award_idempotent_once_per_friend() -> None:
    async def main() -> None:
        repo = InMemoryReferralBonusAwardRepository()
        # First qualifying payment by friend → award granted.
        assert await repo.award_if_absent(_bonus_record(referrer="ref", payer="friend")) is True
        # Same friend pays again → idempotent no-op (UNIQUE referrer+payer).
        assert await repo.award_if_absent(_bonus_record(referrer="ref", payer="friend")) is False
        # A different friend → separate award.
        assert await repo.award_if_absent(_bonus_record(referrer="ref", payer="friend2")) is True

    _run(main())


def test_bonus_sum_awarded_days() -> None:
    async def main() -> None:
        repo = InMemoryReferralBonusAwardRepository()
        assert await repo.sum_awarded_days("ref") == 0
        await repo.award_if_absent(_bonus_record(referrer="ref", payer="f1"))
        await repo.award_if_absent(_bonus_record(referrer="ref", payer="f2"))
        await repo.award_if_absent(_bonus_record(referrer="ref", payer="f1"))  # dup → ignored
        # 2 distinct friends × 30 days = 60.
        assert await repo.sum_awarded_days("ref") == 60
        # Isolated per referrer.
        assert await repo.sum_awarded_days("other") == 0

    _run(main())


def test_invitee_bonus_award_is_once_per_referred_user() -> None:
    async def main() -> None:
        repo = InMemoryReferralInviteeBonusAwardRepository()
        record = ReferralInviteeBonusAwardRecord(
            award_id="invitee-1",
            referred_user_id="friend",
            referrer_user_id="referrer",
            days_awarded=REFERRAL_INVITEE_BONUS_DAYS,
            related_plan_id="1m",
            correlation_id="cid",
            created_at=datetime.now(UTC),
        )
        assert await repo.award_if_absent(record) is True
        assert await repo.award_if_absent(record) is False

    _run(main())
