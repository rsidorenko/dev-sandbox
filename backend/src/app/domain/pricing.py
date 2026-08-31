"""Глобальная скидка и эффективная цена: чистая доменная логика.

Глобальная скидка — административная скидка «всем сразу», без промокода и без
учёта погашений (redemption). Хранится синглтоном в таблице pricing_global_discount,
действует ТОЛЬКО на 7 фиксированных тарифов (FIXED_PLAN_IDS), никогда — на
add-device платежи и «Свой тариф» (custom:*).

Правило комбинирования с промокодом пользователя — best-of: обе скидки считаются
на одной базе, действует большая; при равенстве выигрывает промокод (сохраняет
семантику redemption-журнала и metadata promo_code у платежа).

Математика сознательно зеркалит apply_promo_discount (округление half-up до
целого рубля — требование границы YooKassa, клэмп итога в [1 рубль, база]).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.promo import (
    FIXED_PLAN_IDS,
    PromoCode,
    PromoDiscountKind,
    PromoPrice,
    _round_half_up,
    apply_promo_discount,
    validate_discount,
)

# Кто дал итоговую скидку в effective_plan_price.
WINNER_NONE = "none"
WINNER_PROMO = "promo"
WINNER_GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class GlobalDiscount:
    """Состояние глобальной скидки (зеркало синглтона pricing_global_discount)."""

    is_active: bool
    discount_kind: PromoDiscountKind
    discount_value: int
    # Публичная подпись для витрины (например «Акция»); None — без подписи.
    public_label: str | None = None

    @property
    def is_noop(self) -> bool:
        """Скидка заведомо ничего не меняет (выключена) — можно не считать."""
        return not self.is_active


@dataclass(frozen=True, slots=True)
class EffectivePrice:
    """Итог применения скидок к базе (целые рубли): best-of(промо, глобальная)."""

    base_rubles: int
    discounted_rubles: int
    discount_kopecks: int
    winner: str  # WINNER_NONE | WINNER_PROMO | WINNER_GLOBAL

    @property
    def discounted_kopecks(self) -> int:
        return self.discounted_rubles * 100


def validate_global_discount(kind: PromoDiscountKind, value: int) -> str | None:
    """Валидация глобальной скидки. Делегирует promo.validate_discount — единые
    правила с промокодами (значение положительное, процент ≤ 100, фикс ≥ 1 ₽)."""
    return validate_discount(kind, value)


def global_discount_label(gd: GlobalDiscount) -> str:
    """Короткая подпись скидки для бейджей/кнопок: «−20%», «−200 ₽».

    Стиль повторяет promo_badge_label (storefront_ui), чтобы витрины выглядели
    одинаково и с промокодом, и с глобальной скидкой.
    """
    if gd.discount_kind == PromoDiscountKind.PERCENT:
        return f"−{gd.discount_value}%"
    return f"−{gd.discount_value} ₽"


def apply_global_discount(
    base_rubles: int,
    gd: GlobalDiscount | None,
    *,
    plan_id: str | None = None,
    is_addon: bool = False,
) -> PromoPrice:
    """Применить глобальную скидку к базовой цене (целые рубли).

    Гарантии зеркалят apply_promo_discount:
    - base_rubles <= 0 → нули (нечего скидывать);
    - скидка выключена (None/is_active=False) → полная цена;
    - применяется только к подписке фиксированного тарифа (plan_id в
      FIXED_PLAN_IDS): add-device и «Свой» (custom:*) — всегда полная цена;
    - итог округляется half-up до целого рубля, клэмпится в [1, base].
    """
    if base_rubles <= 0:
        return PromoPrice(base_rubles=0, discounted_rubles=0, discount_kopecks=0)
    if gd is None or gd.is_noop:
        return PromoPrice(
            base_rubles=base_rubles,
            discounted_rubles=base_rubles,
            discount_kopecks=0,
        )
    if is_addon or not plan_id or plan_id not in FIXED_PLAN_IDS:
        return PromoPrice(
            base_rubles=base_rubles,
            discounted_rubles=base_rubles,
            discount_kopecks=0,
        )
    if gd.discount_kind == PromoDiscountKind.PERCENT:
        discounted = _round_half_up(base_rubles * (100 - gd.discount_value) / 100)
    else:  # PromoDiscountKind.FIXED_RUBLES
        discounted = base_rubles - gd.discount_value
    discounted = max(1, min(discounted, base_rubles))
    return PromoPrice(
        base_rubles=base_rubles,
        discounted_rubles=discounted,
        discount_kopecks=(base_rubles - discounted) * 100,
    )


def effective_plan_price(
    base_rubles: int,
    *,
    plan_id: str | None = None,
    promo: PromoCode | None = None,
    global_discount: GlobalDiscount | None = None,
    is_addon: bool = False,
) -> EffectivePrice:
    """Эффективная цена: best-of из промокода и глобальной скидки.

    Обе скидки считаются на одной базе; действует большая (меньший итог).
    При равенстве выигрывает промокод — тогда metadata платежа сохраняет
    promo_code и redemption-журнал остаётся корректным.

    winner = WINNER_PROMO/WINNER_GLOBAL только при реальной скидке (> 0);
    если эффективная скидка нулевая (в т.ч. per-tariff промокод без записи
    для этого тарифа), winner = WINNER_NONE и итог равен базе.
    """
    if base_rubles <= 0:
        return EffectivePrice(base_rubles=0, discounted_rubles=0, discount_kopecks=0, winner=WINNER_NONE)
    promo_price = (
        apply_promo_discount(base_rubles, promo, plan_id=plan_id, is_addon=is_addon) if promo is not None else None
    )
    global_price = apply_global_discount(base_rubles, global_discount, plan_id=plan_id, is_addon=is_addon)
    promo_discounted = promo_price.discounted_rubles if promo_price is not None else base_rubles
    global_discounted = global_price.discounted_rubles

    if promo_price is not None and promo_discounted <= global_discounted:
        # Промо выиграл или ничья (tie → промо).
        if promo_discounted < base_rubles:
            return EffectivePrice(
                base_rubles=base_rubles,
                discounted_rubles=promo_discounted,
                discount_kopecks=promo_price.discount_kopecks,
                winner=WINNER_PROMO,
            )
    elif global_discounted < base_rubles:
        return EffectivePrice(
            base_rubles=base_rubles,
            discounted_rubles=global_discounted,
            discount_kopecks=global_price.discount_kopecks,
            winner=WINNER_GLOBAL,
        )
    return EffectivePrice(
        base_rubles=base_rubles,
        discounted_rubles=base_rubles,
        discount_kopecks=0,
        winner=WINNER_NONE,
    )
