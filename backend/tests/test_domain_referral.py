"""Tests for domain.referral: commission rates and calculations."""

from app.domain.referral import (
    ReferralCommission,
    build_commissions_for_payment,
    calculate_commission_kopecks,
    level1_commission_rate,
    level2_commission_rate,
    resolve_direct_and_indirect_referrers,
    rubles_from_kopecks,
)
from app.application.referral_handler import apply_referral_on_registration, get_referral_info
from app.persistence.in_memory import (
    InMemoryReferralBalanceRepository,
    InMemoryReferralCodeRepository,
    InMemoryReferralRelationshipRepository,
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


def test_unknown_plan_rate():
    assert level1_commission_rate("99m") == 0.0
    assert level2_commission_rate("99m") == 0.0


def test_calculate_commission_kopecks():
    assert calculate_commission_kopecks(30000, 0.35) == 10500  # 300 RUB * 35% = 105 RUB


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

    _run(main())


def test_apply_referral_creates_l2_when_referrer_has_own_referrer() -> None:
    async def main() -> None:
        code_repo = InMemoryReferralCodeRepository()
        rel_repo = InMemoryReferralRelationshipRepository()

        # Create grand-referrer → referrer → new user chain
        grand = await code_repo.get_or_create("user_grand")
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
            bot_username="bot",
            site_base_url="https://site.example",
        )
        # The referrer invited 2 — must show 2, not 0 (the old find_referrers-based
        # count returned 0 because the referrer was never referred by anyone).
        assert info.direct_referrals_count == 2

    _run(main())
