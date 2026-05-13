"""Tests for domain.referral: commission rates and calculations."""

from app.domain.referral import (
    build_commissions_for_payment,
    calculate_commission_kopecks,
    level1_commission_rate,
    level2_commission_rate,
    rubles_from_kopecks,
)


def test_level1_rates():
    assert level1_commission_rate("1m") == 0.35
    assert level1_commission_rate("3m") == 0.30
    assert level1_commission_rate("6m") == 0.25


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
    assert l1.amount_kopecks == 135000 * 25 // 100  # 33750
    assert l2.amount_kopecks == 135000 * 2 // 100  # 2700
