"""Purchase flow handler: plan selection, device count, price calculation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.devices import extra_device_cost, extra_device_count, validate_device_count
from app.domain.plans import (
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
    return tuple(
        PurchasePlanOption(
            plan_id=p.plan_id.value,
            display_name=plan_display_name(p.plan_id.value),
            price_rubles=p.price_rubles,
            duration_days=p.duration_days,
        )
        for p in get_all_plans()
    )


def build_purchase_summary(plan_id: str, device_count: int) -> PurchaseSummary | str:
    plan = get_plan(plan_id)
    if plan is None:
        return "Тариф не найден"
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
