"""Subscription plans: pricing, duration, and total price calculation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PlanId(StrEnum):
    ONE_MONTH = "1m"
    THREE_MONTHS = "3m"
    SIX_MONTHS = "6m"


@dataclass(frozen=True, slots=True)
class SubscriptionPlan:
    plan_id: PlanId
    duration_months: int
    price_rubles: int
    default_device_limit: int
    extra_device_price_rubles: int


_PLANS: dict[PlanId, SubscriptionPlan] = {
    PlanId.ONE_MONTH: SubscriptionPlan(
        plan_id=PlanId.ONE_MONTH,
        duration_months=1,
        price_rubles=300,
        default_device_limit=5,
        extra_device_price_rubles=80,
    ),
    PlanId.THREE_MONTHS: SubscriptionPlan(
        plan_id=PlanId.THREE_MONTHS,
        duration_months=3,
        price_rubles=750,
        default_device_limit=5,
        extra_device_price_rubles=80,
    ),
    PlanId.SIX_MONTHS: SubscriptionPlan(
        plan_id=PlanId.SIX_MONTHS,
        duration_months=6,
        price_rubles=1350,
        default_device_limit=5,
        extra_device_price_rubles=80,
    ),
}

EXTRA_DEVICE_PRICE_RUBLES = 80
DEFAULT_DEVICE_LIMIT = 5


def get_plan(plan_id: str) -> SubscriptionPlan | None:
    try:
        return _PLANS[PlanId(plan_id)]
    except (ValueError, KeyError):
        return None


def get_all_plans() -> tuple[SubscriptionPlan, ...]:
    return tuple(_PLANS.values())


def calculate_total_price(plan: SubscriptionPlan, device_count: int) -> int:
    extra = max(0, device_count - plan.default_device_limit)
    return plan.price_rubles + extra * plan.extra_device_price_rubles


def calculate_total_price_kopecks(plan: SubscriptionPlan, device_count: int) -> int:
    return calculate_total_price(plan, device_count) * 100


def plan_display_name(plan_id: str) -> str:
    names = {
        PlanId.ONE_MONTH: "1 месяц",
        PlanId.THREE_MONTHS: "3 месяца",
        PlanId.SIX_MONTHS: "6 месяцев",
    }
    try:
        return names[PlanId(plan_id)]
    except (ValueError, KeyError):
        return plan_id
