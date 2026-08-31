"""Purchase flow handler: plan selection, device count, price calculation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from app.domain.devices import extra_device_cost, extra_device_count, validate_device_count
from app.domain.plans import (
    SubscriptionPlan,
    calculate_total_price,
    get_all_plans,
    get_plan,
    plan_display_name,
)


class PurchaseFlowStep(StrEnum):
    SELECT_PLAN = "select_plan"
    SELECT_DEVICES = "select_devices"
    CONFIRM = "confirm"
    PAYMENT = "payment"


@dataclass(frozen=True, slots=True)
class PurchasePlanOption:
    plan_id: str
    display_name: str
    price_rubles: int
    duration_days: int


@dataclass(frozen=True, slots=True)
class PurchaseSummary:
    plan_id: str
    plan_display_name: str
    device_count: int
    base_price_rubles: int
    extra_devices: int
    extra_device_cost_rubles: int
    total_price_rubles: int
    duration_days: int


def get_available_plans() -> tuple[PurchasePlanOption, ...]:
    """Планы по код-дефолтам (sync). Витрина с runtime-ценами —
    ``plan_options_from_plans(await pricing.get_fixed_plans())``."""
    return plan_options_from_plans(get_all_plans())


def plan_options_from_plans(plans: Iterable[SubscriptionPlan]) -> tuple[PurchasePlanOption, ...]:
    """Витрина планов из уже разрешённых планов (код-дефолт ИЛИ админ-оверрайд)."""
    return tuple(
        PurchasePlanOption(
            plan_id=p.plan_id.value,
            display_name=plan_display_name(p.plan_id.value),
            price_rubles=p.price_rubles,
            duration_days=p.duration_days,
        )
        for p in plans
    )


def build_purchase_summary(plan_id: str, device_count: int) -> PurchaseSummary | str:
    """Сводка по код-дефолту плана (sync). Эффективная цена —
    ``build_purchase_summary_for_plan(await pricing.get_plan(plan_id), ...)``."""
    plan = get_plan(plan_id)
    if plan is None:
        return "Тариф не найден"
    return build_purchase_summary_for_plan(plan, device_count)


def build_purchase_summary_for_plan(plan: SubscriptionPlan, device_count: int) -> PurchaseSummary | str:
    """Сводка заказа по уже разрешённому плану (эффективная цена — у вызывающего)."""
    plan_id = plan.plan_id.value
    err = validate_device_count(device_count)
    if err is not None:
        return err
    extra = extra_device_count(device_count, plan.default_device_limit)
    extra_cost = extra_device_cost(device_count, plan.default_device_limit, plan.duration_days)
    total = calculate_total_price(plan, device_count)
    return PurchaseSummary(
        plan_id=plan_id,
        plan_display_name=plan_display_name(plan_id),
        device_count=device_count,
        base_price_rubles=plan.price_rubles,
        extra_devices=extra,
        extra_device_cost_rubles=extra_cost,
        total_price_rubles=total,
        duration_days=plan.duration_days,
    )
