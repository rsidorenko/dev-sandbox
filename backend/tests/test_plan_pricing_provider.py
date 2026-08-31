"""Unit-тесты провайдера runtime-цен и расчёта эффективного списания (без БД)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.application.plan_pricing import (
    PlanPricingProvider,
    charge_metadata_fields,
    get_plan_pricing,
    invalidate_all_plan_pricing,
    resolve_effective_charge,
)
from app.domain.pricing import WINNER_GLOBAL, WINNER_NONE, GlobalDiscount
from app.domain.promo import PromoAudience, PromoCode, PromoDiscountKind


def _gd(
    *,
    kind: PromoDiscountKind = PromoDiscountKind.PERCENT,
    value: int = 20,
    is_active: bool = True,
) -> GlobalDiscount:
    return GlobalDiscount(is_active=is_active, discount_kind=kind, discount_value=value)


def _promo(*, discount_value: int = 10) -> PromoCode:
    now = datetime.now(UTC)
    return PromoCode(
        code="WELCOME",
        audience=PromoAudience.ALL_USERS,
        discount_kind=PromoDiscountKind.PERCENT,
        discount_value=discount_value,
        max_redemptions=None,
        redemptions_count=0,
        expires_at=None,
        is_active=True,
        note=None,
        created_at=now,
        updated_at=now,
        created_by="admin",
    )


def _fake_loader(overrides: dict[str, int] | None = None, gd: GlobalDiscount | None = None):
    calls = {"count": 0}

    async def load() -> tuple[dict[str, int], GlobalDiscount | None]:
        calls["count"] += 1
        return dict(overrides or {}), gd

    return load, calls


def _failing_loader():
    calls = {"count": 0}

    async def load() -> tuple[dict[str, int], GlobalDiscount | None]:
        calls["count"] += 1
        raise RuntimeError("db down")

    return load, calls


# --- дефолты и оверрайды ---


@pytest.mark.asyncio
async def test_pool_none_returns_code_defaults() -> None:
    provider = PlanPricingProvider(None)
    plan = await provider.get_plan("1m")
    assert plan is not None and plan.price_rubles == 249
    assert len(await provider.get_fixed_plans()) == 7
    assert await provider.get_global_discount() is None


@pytest.mark.asyncio
async def test_override_applied_to_plan() -> None:
    loader, calls = _fake_loader(overrides={"1m": 199})
    provider = PlanPricingProvider(None, loader=loader)
    plan = await provider.get_plan("1m")
    assert plan is not None and plan.price_rubles == 199
    other = await provider.get_plan("3m")
    assert other is not None and other.price_rubles == 699  # без оверрайда — дефолт
    assert calls["count"] == 1  # один снимок закэширован


@pytest.mark.asyncio
async def test_custom_plan_passes_through() -> None:
    loader, _ = _fake_loader(overrides={"1m": 199})
    provider = PlanPricingProvider(None, loader=loader)
    plan = await provider.get_plan("custom:45")
    assert plan is not None
    assert plan.price_rubles == 45 * 15  # 15 ₽/день из кода, оверрайды не действуют


@pytest.mark.asyncio
async def test_unknown_plan_returns_none() -> None:
    loader, _ = _fake_loader()
    provider = PlanPricingProvider(None, loader=loader)
    assert await provider.get_plan("99y") is None


@pytest.mark.asyncio
async def test_invalid_override_value_ignored() -> None:
    loader, _ = _fake_loader(overrides={"1m": 0})  # CHECK БД не пустил бы — вторая линия
    provider = PlanPricingProvider(None, loader=loader)
    plan = await provider.get_plan("1m")
    assert plan is not None and plan.price_rubles == 249


@pytest.mark.asyncio
async def test_global_discount_loaded() -> None:
    loader, _ = _fake_loader(gd=_gd())
    provider = PlanPricingProvider(None, loader=loader)
    gd = await provider.get_global_discount()
    assert gd is not None and gd.is_active and gd.discount_value == 20


# --- TTL и инвалидация ---


@pytest.mark.asyncio
async def test_cache_reused_within_ttl() -> None:
    loader, calls = _fake_loader(overrides={"1m": 199})
    provider = PlanPricingProvider(None, ttl_seconds=60.0, loader=loader)
    await provider.get_plan("1m")
    await provider.get_plan("1m")
    await provider.get_state()
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_invalidate_forces_reload() -> None:
    loader, calls = _fake_loader(overrides={"1m": 199})
    provider = PlanPricingProvider(None, ttl_seconds=60.0, loader=loader)
    await provider.get_plan("1m")
    provider.invalidate()
    plan = await provider.get_plan("1m")
    assert plan is not None and plan.price_rubles == 199
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_ttl_expiry_forces_reload() -> None:
    loader, calls = _fake_loader(overrides={"1m": 199})
    provider = PlanPricingProvider(None, ttl_seconds=0.02, loader=loader)
    await provider.get_plan("1m")
    await asyncio.sleep(0.05)
    await provider.get_plan("1m")
    assert calls["count"] == 2


# --- fail-open ---


@pytest.mark.asyncio
async def test_loader_failure_falls_back_to_defaults() -> None:
    loader, _ = _failing_loader()
    provider = PlanPricingProvider(None, loader=loader)
    plan = await provider.get_plan("1m")
    assert plan is not None and plan.price_rubles == 249  # дефолты кода, не исключение


@pytest.mark.asyncio
async def test_loader_failure_cached_within_ttl() -> None:
    loader, calls = _failing_loader()
    provider = PlanPricingProvider(None, ttl_seconds=60.0, loader=loader)
    await provider.get_state()
    await provider.get_state()
    assert calls["count"] == 1  # fallback закэширован — не долбим упавшую БД


# --- реестр per-pool ---


@pytest.mark.asyncio
async def test_registry_singleton_per_pool_and_invalidate_all() -> None:
    class FakePool:
        pass

    pool_a, pool_b = FakePool(), FakePool()
    assert get_plan_pricing(pool_a) is get_plan_pricing(pool_a)
    assert get_plan_pricing(pool_a) is not get_plan_pricing(pool_b)
    assert get_plan_pricing(None) is get_plan_pricing(None)
    invalidate_all_plan_pricing()  # не падает на пустом/пустом реестре


# --- расчёт списания и metadata ---


def test_resolve_effective_charge_global_wins() -> None:
    resolved = resolve_effective_charge(
        base_rubles=249, plan_id="1m", promo=_promo(discount_value=10), global_discount=_gd(value=20)
    )
    assert resolved.charge_rubles == 199
    assert resolved.charge_kopecks == 19900
    assert resolved.base_amount_kopecks == 24900
    assert resolved.price.winner == WINNER_GLOBAL


def test_charge_metadata_promo_winner() -> None:
    resolved = resolve_effective_charge(
        base_rubles=249, plan_id="1m", promo=_promo(discount_value=30), global_discount=_gd(value=20)
    )
    meta = charge_metadata_fields(resolved, promo_code="WELCOME", global_discount=_gd(value=20))
    assert meta == {
        "base_amount_kopecks": "24900",
        "promo_code": "WELCOME",
        "discount_kopecks": "7500",
    }


def test_charge_metadata_global_winner_no_promo_code() -> None:
    resolved = resolve_effective_charge(
        base_rubles=249, plan_id="1m", promo=_promo(discount_value=10), global_discount=_gd(value=20)
    )
    meta = charge_metadata_fields(resolved, promo_code="WELCOME", global_discount=_gd(value=20))
    assert "promo_code" not in meta  # глобальная выиграла — промокод не расходуется
    assert meta["discount_kopecks"] == "5000"
    assert meta["global_discount"] == "−20%"
    assert meta["base_amount_kopecks"] == "24900"


def test_charge_metadata_no_discount_only_base() -> None:
    resolved = resolve_effective_charge(base_rubles=249, plan_id="1m", promo=None, global_discount=None)
    assert resolved.price.winner == WINNER_NONE
    meta = charge_metadata_fields(resolved, promo_code=None, global_discount=None)
    assert meta == {"base_amount_kopecks": "24900"}
