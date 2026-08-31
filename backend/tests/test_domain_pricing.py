"""Unit-тесты глобальной скидки и best-of комбинирования (чистые функции, без БД)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.pricing import (
    WINNER_GLOBAL,
    WINNER_NONE,
    WINNER_PROMO,
    EffectivePrice,
    GlobalDiscount,
    apply_global_discount,
    effective_plan_price,
    global_discount_label,
    validate_global_discount,
)
from app.domain.promo import PromoAudience, PromoCode, PromoDiscountKind, PromoPrice


def _promo(
    *,
    discount_kind: PromoDiscountKind = PromoDiscountKind.PERCENT,
    discount_value: int = 15,
    discounts_by_plan: dict[str, int] | None = None,
) -> PromoCode:
    now = datetime.now(UTC)
    return PromoCode(
        code="WELCOME",
        audience=PromoAudience.ALL_USERS,
        discount_kind=discount_kind,
        discount_value=discount_value,
        max_redemptions=None,
        redemptions_count=0,
        expires_at=None,
        is_active=True,
        note=None,
        created_at=now,
        updated_at=now,
        created_by="admin",
        discounts_by_plan=discounts_by_plan,
        max_redemptions_per_user=None,
    )


def _gd(
    *,
    kind: PromoDiscountKind = PromoDiscountKind.PERCENT,
    value: int = 20,
    is_active: bool = True,
    label: str | None = None,
) -> GlobalDiscount:
    return GlobalDiscount(
        is_active=is_active,
        discount_kind=kind,
        discount_value=value,
        public_label=label,
    )


# --- apply_global_discount: математика и границы ---


def test_percent_discount_rounds_half_up() -> None:
    # 249 ₽ −20% = 199.2 → 199 (half-up до целого рубля).
    price = apply_global_discount(249, _gd(value=20), plan_id="1m")
    assert price.base_rubles == 249
    assert price.discounted_rubles == 199
    assert price.discount_kopecks == 5000


def test_fixed_discount_subtracts_rubles() -> None:
    price = apply_global_discount(249, _gd(kind=PromoDiscountKind.FIXED_RUBLES, value=200), plan_id="1m")
    assert price.discounted_rubles == 49
    assert price.discount_kopecks == 20000


def test_fixed_larger_than_base_clamps_to_one_ruble() -> None:
    price = apply_global_discount(249, _gd(kind=PromoDiscountKind.FIXED_RUBLES, value=5000), plan_id="1m")
    assert price.discounted_rubles == 1
    assert price.discount_kopecks == 24800


def test_percent_100_clamps_to_one_ruble() -> None:
    price = apply_global_discount(249, _gd(value=100), plan_id="1m")
    assert price.discounted_rubles == 1


def test_inactive_discount_is_noop() -> None:
    price = apply_global_discount(249, _gd(value=20, is_active=False), plan_id="1m")
    assert price.discounted_rubles == 249
    assert price.discount_kopecks == 0


def test_none_discount_is_noop() -> None:
    price = apply_global_discount(249, None, plan_id="1m")
    assert price.discounted_rubles == 249


def test_nonpositive_base_returns_zeros() -> None:
    price = apply_global_discount(0, _gd(value=20), plan_id="1m")
    assert price == PromoPrice(base_rubles=0, discounted_rubles=0, discount_kopecks=0)


# --- apply_global_discount: охват (только фиксированные тарифы) ---


def test_custom_plan_excluded() -> None:
    price = apply_global_discount(675, _gd(value=20), plan_id="custom:45")
    assert price.discounted_rubles == 675


def test_addon_excluded() -> None:
    price = apply_global_discount(240, _gd(value=20), plan_id="1m", is_addon=True)
    assert price.discounted_rubles == 240


def test_missing_plan_id_excluded() -> None:
    price = apply_global_discount(249, _gd(value=20), plan_id=None)
    assert price.discounted_rubles == 249


def test_all_fixed_plans_covered() -> None:
    from app.domain.promo import FIXED_PLAN_IDS

    for plan_id in FIXED_PLAN_IDS:
        price = apply_global_discount(100, _gd(value=10), plan_id=plan_id)
        assert price.discounted_rubles == 90, plan_id


# --- effective_plan_price: best-of ---


def test_best_of_global_wins() -> None:
    # Промо −10% (249→224), глобальная −20% (249→199) → выигрывает глобальная.
    eff = effective_plan_price(249, plan_id="1m", promo=_promo(discount_value=10), global_discount=_gd(value=20))
    assert eff.winner == WINNER_GLOBAL
    assert eff.discounted_rubles == 199
    assert eff.discount_kopecks == 5000


def test_best_of_promo_wins() -> None:
    # Промо −30% (249→174), глобальная −20% (249→199) → выигрывает промокод.
    eff = effective_plan_price(249, plan_id="1m", promo=_promo(discount_value=30), global_discount=_gd(value=20))
    assert eff.winner == WINNER_PROMO
    assert eff.discounted_rubles == 174


def test_tie_promo_wins() -> None:
    # Обе −50 ₽: ничья → промокод (сохраняем redemption-семантику).
    eff = effective_plan_price(
        249,
        plan_id="1m",
        promo=_promo(discount_kind=PromoDiscountKind.FIXED_RUBLES, discount_value=50),
        global_discount=_gd(kind=PromoDiscountKind.FIXED_RUBLES, value=50),
    )
    assert eff.winner == WINNER_PROMO
    assert eff.discounted_rubles == 199


def test_zero_discount_promo_falls_back_to_global() -> None:
    # Per-tariff промокод без записи для 1m → промо-скидка 0, действует глобальная.
    eff = effective_plan_price(
        249,
        plan_id="1m",
        promo=_promo(discounts_by_plan={"3m": 10}),
        global_discount=_gd(value=20),
    )
    assert eff.winner == WINNER_GLOBAL
    assert eff.discounted_rubles == 199


def test_promo_only() -> None:
    eff = effective_plan_price(249, plan_id="1m", promo=_promo(discount_value=15))
    assert eff.winner == WINNER_PROMO
    assert eff.discounted_rubles == 212  # 249 * 0.85 = 211.65 → 212


def test_global_only() -> None:
    eff = effective_plan_price(249, plan_id="1m", global_discount=_gd(value=20))
    assert eff.winner == WINNER_GLOBAL
    assert eff.discounted_rubles == 199


def test_no_discounts_full_price() -> None:
    eff = effective_plan_price(249, plan_id="1m")
    assert eff.winner == WINNER_NONE
    assert eff.discounted_rubles == 249
    assert eff.discount_kopecks == 0


def test_global_excluded_custom_plan_still_allows_flat_promo() -> None:
    # custom:N: глобальная не действует, плоский промокод действует (legacy-семантика).
    eff = effective_plan_price(
        675,
        plan_id="custom:45",
        promo=_promo(discount_value=10),
        global_discount=_gd(value=20),
    )
    assert eff.winner == WINNER_PROMO
    assert eff.discounted_rubles == 608  # 675 * 0.9 = 607.5 → 608


def test_addon_neither_applies() -> None:
    eff = effective_plan_price(
        240,
        plan_id="1m",
        promo=_promo(discounts_by_plan={"1m": 20}),  # per-tariff на addon не действует
        global_discount=_gd(value=20),
        is_addon=True,
    )
    assert eff.winner == WINNER_NONE
    assert eff.discounted_rubles == 240


def test_discounted_kopecks_property() -> None:
    eff = effective_plan_price(249, plan_id="1m", global_discount=_gd(value=20))
    assert eff.discounted_kopecks == 19900


# --- подписи и валидация ---


def test_global_discount_label_percent() -> None:
    assert global_discount_label(_gd(value=20)) == "−20%"


def test_global_discount_label_fixed() -> None:
    assert global_discount_label(_gd(kind=PromoDiscountKind.FIXED_RUBLES, value=200)) == "−200 ₽"


def test_validate_global_discount_delegates_to_promo_rules() -> None:
    assert validate_global_discount(PromoDiscountKind.PERCENT, 0) is not None
    assert validate_global_discount(PromoDiscountKind.PERCENT, 101) is not None
    assert validate_global_discount(PromoDiscountKind.FIXED_RUBLES, 50) is None
    assert validate_global_discount(PromoDiscountKind.PERCENT, 100) is None


def test_effective_price_is_frozen_dataclass() -> None:
    eff = EffectivePrice(base_rubles=100, discounted_rubles=80, discount_kopecks=2000, winner=WINNER_GLOBAL)
    assert eff.base_rubles == 100
    assert eff.discounted_kopecks == 8000
