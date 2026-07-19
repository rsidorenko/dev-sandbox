"""Promo/discount code domain: data shape, discount math, validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# YooKassa charges whole rubles, so the floor is 1 ₽ (a discount never makes a payment free).
MIN_PAYMENT_RUBLES = 1


@dataclass(frozen=True, slots=True)
class PromoCode:
    code: str
    owner_internal_user_id: str | None
    discount_percent: int
    valid_from: datetime
    valid_until: datetime | None
    max_uses: int
    uses_count: int
    active: bool


class PromoValidationError(StrEnum):
    NOT_FOUND = "not_found"
    INACTIVE = "inactive"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    EXHAUSTED = "exhausted"


def calculate_discounted_rubles(price_rubles: int, discount_percent: int) -> int:
    """Apply a percent discount to a whole-ruble price, floored at MIN_PAYMENT_RUBLES (1 ₽).
    Returns whole rubles (YooKassa charges whole-ruble amounts)."""
    if discount_percent <= 0:
        return price_rubles
    if discount_percent >= 100:
        return MIN_PAYMENT_RUBLES
    discounted = round(price_rubles * (100 - discount_percent) / 100)
    return max(discounted, MIN_PAYMENT_RUBLES)


def validate_promo(promo: PromoCode | None, *, now: datetime) -> PromoValidationError | None:
    """Return the first validation failure, or None if the code is currently usable."""
    if promo is None:
        return PromoValidationError.NOT_FOUND
    if not promo.active:
        return PromoValidationError.INACTIVE
    if now < promo.valid_from:
        return PromoValidationError.NOT_YET_VALID
    if promo.valid_until is not None and now >= promo.valid_until:
        return PromoValidationError.EXPIRED
    if promo.uses_count >= promo.max_uses:
        return PromoValidationError.EXHAUSTED
    return None
