"""Tests for domain.plans: tariffs, price calculation."""

from app.domain.plans import (
    CUSTOM_DAY_PRICE_RUBLES,
    DEFAULT_DEVICE_LIMIT,
    PlanId,
    calculate_total_price,
    get_all_plans,
    get_plan,
    make_custom_plan_id,
    plan_display_name,
)


def test_get_plan_1m():
    plan = get_plan("1m")
    assert plan is not None
    assert plan.duration_days == 30
    assert plan.price_rubles == 249
    assert plan.default_device_limit == 5
    assert plan.extra_device_price_rubles == 80


def test_get_plan_3m():
    plan = get_plan("3m")
    assert plan is not None
    assert plan.duration_days == 90
    assert plan.price_rubles == 699


def test_get_plan_6m():
    plan = get_plan("6m")
    assert plan is not None
    assert plan.duration_days == 180
    assert plan.price_rubles == 1259


def test_get_plan_1d():
    plan = get_plan("1d")
    assert plan is not None
    assert plan.duration_days == 1
    assert plan.price_rubles == 12


def test_get_plan_7d():
    plan = get_plan("7d")
    assert plan is not None
    assert plan.duration_days == 7
    assert plan.price_rubles == 99


def test_get_plan_14d():
    plan = get_plan("14d")
    assert plan is not None
    assert plan.duration_days == 14
    assert plan.price_rubles == 169


def test_get_plan_365d():
    plan = get_plan("365d")
    assert plan is not None
    assert plan.duration_days == 365
    assert plan.price_rubles == 2199


def test_get_plan_unknown():
    assert get_plan("99m") is None


def test_get_all_plans():
    plans = get_all_plans()
    assert len(plans) == 7
    ids = {p.plan_id for p in plans}
    assert ids == {
        PlanId.ONE_DAY,
        PlanId.SEVEN_DAYS,
        PlanId.TWO_WEEKS,
        PlanId.ONE_MONTH,
        PlanId.THREE_MONTHS,
        PlanId.SIX_MONTHS,
        PlanId.ONE_YEAR,
    }


def test_calculate_total_price_default_devices():
    plan = get_plan("1m")
    assert calculate_total_price(plan, 5) == 249


def test_calculate_total_price_extra_devices():
    plan = get_plan("1m")
    # extra 2 devices × 80₽/30 per day × 30 days = 2 × 80 = 160
    assert calculate_total_price(plan, 7) == 249 + 2 * round(80 / 30 * 30)


def test_calculate_total_price_no_extra():
    plan = get_plan("3m")
    assert calculate_total_price(plan, 5) == 699


def test_calculate_total_price_many_extra():
    plan = get_plan("6m")
    # 5 extra × 80₽/30 per day × 180 days
    extra = 5 * round(80 / 30 * 180)  # 2400
    assert calculate_total_price(plan, 10) == 1259 + extra


def test_custom_plan():
    plan_id = make_custom_plan_id(45)
    plan = get_plan(plan_id)
    assert plan is not None
    assert plan.duration_days == 45
    assert plan.price_rubles == 45 * CUSTOM_DAY_PRICE_RUBLES  # 675


def test_custom_plan_invalid():
    assert get_plan("custom:0") is None
    assert get_plan("custom:366") is None
    assert get_plan("custom:abc") is None


def test_plan_display_name():
    assert plan_display_name("1d") == "1 день"
    assert plan_display_name("7d") == "7 дней"
    assert plan_display_name("14d") == "2 недели"
    assert plan_display_name("1m") == "1 месяц"
    assert plan_display_name("3m") == "3 месяца"
    assert plan_display_name("6m") == "6 месяцев"
    assert plan_display_name("365d") == "1 год"


def test_plan_display_name_custom():
    assert plan_display_name("custom:45") == "45 дней"
    assert plan_display_name("custom:1") == "1 день"
    assert plan_display_name("custom:21") == "21 день"
    assert plan_display_name("custom:60") == "2 месяца"


def test_default_device_limit_is_5():
    assert DEFAULT_DEVICE_LIMIT == 5
