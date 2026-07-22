"""Unit-тесты доменной логики промокодов (чистые функции, без БД)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.promo import (
    PROMO_APPLIED_TTL_HOURS,
    PromoAudience,
    PromoCode,
    PromoDiscountKind,
    apply_promo_discount,
    audience_eligible,
    is_expired,
    is_new_user_eligible,
    normalize_code,
    redeem_block_reason,
    validate_code_format,
    validate_discount,
)


def _promo(
    *,
    code: str = "WELCOME",
    audience: PromoAudience = PromoAudience.NEW_USERS,
    discount_kind: PromoDiscountKind = PromoDiscountKind.PERCENT,
    discount_value: int = 15,
    max_redemptions: int | None = None,
    redemptions_count: int = 0,
    expires_at: datetime | None = None,
    is_active: bool = True,
) -> PromoCode:
    now = datetime.now(UTC)
    return PromoCode(
        code=code,
        audience=audience,
        discount_kind=discount_kind,
        discount_value=discount_value,
        max_redemptions=max_redemptions,
        redemptions_count=redemptions_count,
        expires_at=expires_at,
        is_active=is_active,
        note=None,
        created_at=now,
        updated_at=now,
        created_by="admin",
    )


# --- apply_promo_discount: математика и инварианты сверки ---


def test_apply_percent_rounds_half_up_to_whole_ruble():
    # 15% от 249 = 211.65 -> round half-up = 212. discount_kopecks = (249-212)*100 = 3700.
    # Именно 3700 кладётся в metadata и вычитается вебхуком -> expected = 24900 - 3700 = 21200 = charged.
    price = apply_promo_discount(249, _promo(discount_kind=PromoDiscountKind.PERCENT, discount_value=15))
    assert price.base_rubles == 249
    assert price.discounted_rubles == 212
    assert price.discount_kopecks == 3700
    assert price.discounted_kopecks == 21200
    # Свёрка: base_kopecks - discount_kopecks == discounted_kopecks (round-trip вебхука).
    assert price.base_rubles * 100 - price.discount_kopecks == price.discounted_kopecks


def test_apply_percent_half_up_rounds_up_on_exact_half():
    # 50% от 249 = 124.5 -> half-up = 125.
    price = apply_promo_discount(249, _promo(discount_kind=PromoDiscountKind.PERCENT, discount_value=50))
    assert price.discounted_rubles == 125
    assert price.discount_kopecks == 12400


def test_apply_fixed_rubles():
    price = apply_promo_discount(249, _promo(discount_kind=PromoDiscountKind.FIXED_RUBLES, discount_value=50))
    assert price.discounted_rubles == 199
    assert price.discount_kopecks == 5000


def test_apply_clamps_over_base_fixed_to_one_ruble():
    # Фикс 300 на базе 249 -> итог не может быть 0/отрицательным: платит 1 рубль.
    price = apply_promo_discount(249, _promo(discount_kind=PromoDiscountKind.FIXED_RUBLES, discount_value=300))
    assert price.discounted_rubles == 1
    assert price.discount_kopecks == 24800


def test_apply_clamps_100_percent_to_one_ruble():
    # 100% технически = 0, но YooKassa не примет 0.00 -> минимум 1 рубль (v1 ограничение).
    price = apply_promo_discount(249, _promo(discount_kind=PromoDiscountKind.PERCENT, discount_value=100))
    assert price.discounted_rubles == 1
    assert price.discount_kopecks == 24800


def test_apply_zero_base_is_noop():
    # add-device без доплаты (base 0) — скидывать нечего.
    price = apply_promo_discount(0, _promo(discount_kind=PromoDiscountKind.PERCENT, discount_value=50))
    assert price.discounted_rubles == 0
    assert price.discount_kopecks == 0


def test_apply_discounted_never_above_base():
    # Минимальная фикс-скидка: итог ниже базы и >= 1 рубля.
    price = apply_promo_discount(249, _promo(discount_kind=PromoDiscountKind.FIXED_RUBLES, discount_value=1))
    assert 1 <= price.discounted_rubles <= 249
    assert price.discounted_rubles == 248


def test_discount_round_trip_consistency_many_plans():
    # Для всех тарифов и набора процентов: base - discount_kopecks/100 == discounted_rubles.
    bases = [12, 99, 169, 249, 699, 1259, 2199, 45]  # включая custom:45
    for base in bases:
        for pct in (1, 5, 10, 15, 25, 33, 50, 75, 99):
            price = apply_promo_discount(base, _promo(discount_kind=PromoDiscountKind.PERCENT, discount_value=pct))
            assert price.base_rubles * 100 - price.discount_kopecks == price.discounted_kopecks
            assert 1 <= price.discounted_rubles <= base


# --- is_new_user_eligible: корректный предикат «нового» ---


def test_new_user_when_never_converted():
    assert is_new_user_eligible(trial_used=False, has_active_subscription=False, has_accepted_payment=False) is True


def test_paid_then_expired_is_not_new():
    # КОРРЕКТНОЕ поведение: юзер платил (картой), подписка истекла -> НЕ новый,
    # хотя trial_used=False и state_label!='active'. Без признака оплаты прошёл бы как новый.
    assert is_new_user_eligible(trial_used=False, has_active_subscription=False, has_accepted_payment=True) is False


def test_trial_used_is_not_new():
    assert is_new_user_eligible(trial_used=True, has_active_subscription=False, has_accepted_payment=False) is False


def test_active_subscription_is_not_new():
    assert is_new_user_eligible(trial_used=False, has_active_subscription=True, has_accepted_payment=False) is False


# --- audience_eligible ---


def test_audience_new_users_strict():
    assert audience_eligible(PromoAudience.NEW_USERS, is_new_user=True) is True
    assert audience_eligible(PromoAudience.NEW_USERS, is_new_user=False) is False


def test_audience_existing_users_strict():
    # existing = строго «не новые» (доп. защита от abuse).
    assert audience_eligible(PromoAudience.EXISTING_USERS, is_new_user=False) is True
    assert audience_eligible(PromoAudience.EXISTING_USERS, is_new_user=True) is False


# --- redeem_block_reason / is_expired ---


def test_block_reason_inactive():
    assert redeem_block_reason(_promo(is_active=False)) == "promo_inactive"


def test_block_reason_expired():
    past = datetime.now(UTC) - timedelta(hours=1)
    assert redeem_block_reason(_promo(expires_at=past)) == "promo_expired"


def test_block_reason_exhausted():
    assert redeem_block_reason(_promo(max_redemptions=10, redemptions_count=10)) == "promo_exhausted"


def test_block_reason_none_for_redeemable():
    future = datetime.now(UTC) + timedelta(days=1)
    assert redeem_block_reason(_promo(max_redemptions=10, redemptions_count=3, expires_at=future)) is None


def test_is_expired_none_means_never():
    assert is_expired(None) is False
    assert is_expired(datetime.now(UTC) + timedelta(days=1)) is False


# --- validation ---


def test_validate_code_format_accepts_and_normalizes():
    assert validate_code_format("welcome") is None
    assert normalize_code("  welcome  ") == "WELCOME"


def test_validate_code_format_rejects_bad():
    assert validate_code_format("") is not None  # пусто
    assert validate_code_format("AB") is not None  # короткий
    assert validate_code_format("A-B") is not None  # спецсимвол
    assert validate_code_format("ТОМ") is not None  # кириллица


def test_validate_discount_percent_bounds():
    assert validate_discount(PromoDiscountKind.PERCENT, 0) is not None
    assert validate_discount(PromoDiscountKind.PERCENT, 101) is not None
    assert validate_discount(PromoDiscountKind.PERCENT, 50) is None


def test_validate_discount_fixed_positive():
    assert validate_discount(PromoDiscountKind.FIXED_RUBLES, 0) is not None
    assert validate_discount(PromoDiscountKind.FIXED_RUBLES, 100) is None


def test_promo_applied_ttl_constant():
    assert PROMO_APPLIED_TTL_HOURS == 24
