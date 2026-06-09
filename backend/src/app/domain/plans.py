"""Subscription plans: pricing, duration, and total price calculation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class PlanId(StrEnum):
    ONE_DAY = "1d"
    SEVEN_DAYS = "7d"
    TWO_WEEKS = "14d"
    ONE_MONTH = "1m"
    THREE_MONTHS = "3m"
    SIX_MONTHS = "6m"
    ONE_YEAR = "365d"


@dataclass(frozen=True, slots=True)
class SubscriptionPlan:
    plan_id: PlanId
    duration_days: int
    price_rubles: int
    default_device_limit: int
    extra_device_price_rubles: int


_PLANS: dict[PlanId, SubscriptionPlan] = {
    PlanId.ONE_DAY: SubscriptionPlan(
        plan_id=PlanId.ONE_DAY,
        duration_days=1,
        price_rubles=12,
        default_device_limit=5,
        extra_device_price_rubles=80,
    ),
    PlanId.SEVEN_DAYS: SubscriptionPlan(
        plan_id=PlanId.SEVEN_DAYS,
        duration_days=7,
        price_rubles=99,
        default_device_limit=5,
        extra_device_price_rubles=80,
    ),
    PlanId.TWO_WEEKS: SubscriptionPlan(
        plan_id=PlanId.TWO_WEEKS,
        duration_days=14,
        price_rubles=169,
        default_device_limit=5,
        extra_device_price_rubles=80,
    ),
    PlanId.ONE_MONTH: SubscriptionPlan(
        plan_id=PlanId.ONE_MONTH,
        duration_days=30,
        price_rubles=249,
        default_device_limit=5,
        extra_device_price_rubles=80,
    ),
    PlanId.THREE_MONTHS: SubscriptionPlan(
        plan_id=PlanId.THREE_MONTHS,
        duration_days=90,
        price_rubles=699,
        default_device_limit=5,
        extra_device_price_rubles=80,
    ),
    PlanId.SIX_MONTHS: SubscriptionPlan(
        plan_id=PlanId.SIX_MONTHS,
        duration_days=180,
        price_rubles=1259,
        default_device_limit=5,
        extra_device_price_rubles=80,
    ),
    PlanId.ONE_YEAR: SubscriptionPlan(
        plan_id=PlanId.ONE_YEAR,
        duration_days=365,
        price_rubles=2199,
        default_device_limit=5,
        extra_device_price_rubles=80,
    ),
}

EXTRA_DEVICE_PRICE_RUBLES = 80
DEFAULT_DEVICE_LIMIT = int(os.environ.get("DEFAULT_DEVICE_LIMIT", "5"))
CUSTOM_DAY_PRICE_RUBLES = 15


def get_plan(plan_id: str) -> SubscriptionPlan | None:
    if plan_id.startswith("custom:"):
        return _parse_custom_plan(plan_id)
    try:
        return _PLANS[PlanId(plan_id)]
    except (ValueError, KeyError):
        return None


def get_all_plans() -> tuple[SubscriptionPlan, ...]:
    return tuple(_PLANS.values())


def _parse_custom_plan(plan_id: str) -> SubscriptionPlan | None:
    """Parse custom plan like 'custom:45' → 45 days × 15₽."""
    try:
        days = int(plan_id.split(":", 1)[1])
    except (ValueError, IndexError):
        return None
    if not (1 <= days <= 365):
        return None
    return SubscriptionPlan(
        plan_id=plan_id,  # type: ignore[arg-type]
        duration_days=days,
        price_rubles=days * CUSTOM_DAY_PRICE_RUBLES,
        default_device_limit=DEFAULT_DEVICE_LIMIT,
        extra_device_price_rubles=EXTRA_DEVICE_PRICE_RUBLES,
    )


def make_custom_plan_id(days: int) -> str:
    return f"custom:{days}"


def calculate_total_price(plan: SubscriptionPlan, device_count: int) -> int:
    extra = max(0, device_count - plan.default_device_limit)
    extra_total = extra * round(plan.extra_device_price_rubles / 30 * plan.duration_days)
    return plan.price_rubles + extra_total


def calculate_total_price_kopecks(plan: SubscriptionPlan, device_count: int) -> int:
    return calculate_total_price(plan, device_count) * 100


def plan_display_name(plan_id: str) -> str:
    names = {
        PlanId.ONE_DAY: "1 день",
        PlanId.SEVEN_DAYS: "7 дней",
        PlanId.TWO_WEEKS: "2 недели",
        PlanId.ONE_MONTH: "1 месяц",
        PlanId.THREE_MONTHS: "3 месяца",
        PlanId.SIX_MONTHS: "6 месяцев",
        PlanId.ONE_YEAR: "1 год",
    }
    normalized = plan_id.removeprefix("plan_")
    if normalized.startswith("custom:"):
        try:
            days = int(normalized.split(":", 1)[1])
        except (ValueError, IndexError):
            return plan_id
        return _format_days(days)
    try:
        return names[PlanId(normalized)]
    except (ValueError, KeyError):
        return plan_id


def _format_days(days: int) -> str:
    if days % 365 == 0 and days >= 365:
        years = days // 365
        return f"{years} {'год' if years == 1 else 'года' if years <= 4 else 'лет'}"
    if days % 30 == 0:
        months = days // 30
        return f"{months} {'месяц' if months == 1 else 'месяца' if months <= 4 else 'месяцев'}"
    if days == 1:
        return "1 день"
    if days % 10 == 1 and days != 11:
        return f"{days} день"
    if 2 <= days % 10 <= 4 and not (12 <= days <= 14):
        return f"{days} дня"
    return f"{days} дней"
