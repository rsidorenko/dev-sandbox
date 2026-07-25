"""Юнит-тесты дескриптора применённого промокода (web_api.promocode pure helpers).

Без БД — проверяем только _discount_label/_applied_descriptor: per-tariff даёт
«по тарифам» и несёт discounts_by_plan (для фронт-расчёта цен), flat — как раньше.
Регресс «−0%»/«0 ₽» для per-tariff.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.promo import PromoAudience, PromoCode, PromoDiscountKind
from app.web_api.promocode import _applied_descriptor, _discount_label

_NOW = datetime.now(UTC)


def _promo(
    *,
    kind: PromoDiscountKind = PromoDiscountKind.PERCENT,
    value: int = 15,
    discounts_by_plan: dict[str, int] | None = None,
) -> PromoCode:
    return PromoCode(
        code="180TEARZ",
        audience=PromoAudience.NEW_USERS,
        discount_kind=kind,
        discount_value=value,
        max_redemptions=None,
        redemptions_count=0,
        expires_at=None,
        is_active=True,
        note=None,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="admin",
        discounts_by_plan=discounts_by_plan,
    )


def test_discount_label_flat():
    assert _discount_label(_promo(kind=PromoDiscountKind.PERCENT, value=15)) == "−15%"
    assert _discount_label(_promo(kind=PromoDiscountKind.FIXED_RUBLES, value=50)) == "−50 ₽"


def test_discount_label_per_tariff_not_zero():
    label = _discount_label(_promo(discounts_by_plan={"1m": 50}))
    assert label == "по тарифам"
    assert "0" not in label


def test_applied_descriptor_includes_discounts_by_plan():
    exp = _NOW + timedelta(hours=24)
    d = _applied_descriptor(_promo(discounts_by_plan={"1m": 10, "3m": 15}), expires_at=exp, valid=True)
    assert d["discounts_by_plan"] == {"1m": 10, "3m": 15}
    assert d["discount_label"] == "по тарифам"
    assert d["code"] == "180TEARZ"
    assert d["valid"] is True


def test_applied_descriptor_flat_null_map():
    exp = _NOW + timedelta(hours=24)
    d = _applied_descriptor(_promo(value=15), expires_at=exp, valid=True)
    assert d["discounts_by_plan"] is None
    assert d["discount_label"] == "−15%"
