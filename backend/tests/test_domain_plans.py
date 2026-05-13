"""Tests for domain.plans: tariffs, price calculation."""

from app.domain.plans import (
    DEFAULT_DEVICE_LIMIT,
    PlanId,
    calculate_total_price,
    get_all_plans,
    get_plan,
    plan_display_name,
)


def test_get_plan_1m():
    plan = get_plan("1m")
    assert plan is not None
    assert plan.duration_months == 1
    assert plan.price_rubles == 300
    assert plan.default_device_limit == 5
    assert plan.extra_device_price_rubles == 80


def test_get_plan_3m():
    plan = get_plan("3m")
    assert plan is not None
    assert plan.duration_months == 3
    assert plan.price_rubles == 750


def test_get_plan_6m():
    plan = get_plan("6m")
    assert plan is not None
    assert plan.duration_months == 6
    assert plan.price_rubles == 1350


def test_get_plan_unknown():
    assert get_plan("99m") is None


def test_get_all_plans():
    plans = get_all_plans()
    assert len(plans) == 3
    ids = {p.plan_id for p in plans}
    assert ids == {PlanId.ONE_MONTH, PlanId.THREE_MONTHS, PlanId.SIX_MONTHS}


def test_calculate_total_price_default_devices():
    plan = get_plan("1m")
    assert calculate_total_price(plan, 5) == 300


def test_calculate_total_price_extra_devices():
    plan = get_plan("1m")
    assert calculate_total_price(plan, 7) == 300 + 2 * 80 * 1  # 460


def test_calculate_total_price_no_extra():
    plan = get_plan("3m")
    assert calculate_total_price(plan, 5) == 750


def test_calculate_total_price_many_extra():
    plan = get_plan("6m")
    assert calculate_total_price(plan, 10) == 1350 + 5 * 80 * 6  # 3750


def test_plan_display_name():
    assert plan_display_name("1m") == "1 месяц"
    assert plan_display_name("3m") == "3 месяца"
    assert plan_display_name("6m") == "6 месяцев"


def test_default_device_limit_is_5():
    assert DEFAULT_DEVICE_LIMIT == 5
