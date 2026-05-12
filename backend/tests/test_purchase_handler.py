"""Tests for application.purchase_handler: purchase flow."""

from app.application.purchase_handler import (
    build_purchase_summary,
    get_available_plans,
)


def test_get_available_plans():
    plans = get_available_plans()
    assert len(plans) == 3
    ids = {p.plan_id for p in plans}
    assert ids == {"1m", "3m", "6m"}


def test_build_purchase_summary_default():
    result = build_purchase_summary("1m", 5)
    assert isinstance(result, type(result))  # it's a PurchaseSummary
    assert result.plan_id == "1m"
    assert result.device_count == 5
    assert result.total_price_rubles == 300
    assert result.extra_devices == 0
    assert result.extra_device_cost_rubles == 0


def test_build_purchase_summary_with_extra_devices():
    result = build_purchase_summary("1m", 8)
    assert result.total_price_rubles == 300 + 3 * 80  # 540
    assert result.extra_devices == 3
    assert result.extra_device_cost_rubles == 240


def test_build_purchase_summary_invalid_plan():
    result = build_purchase_summary("99m", 5)
    assert isinstance(result, str)
    assert "не найден" in result


def test_build_purchase_summary_invalid_device_count():
    result = build_purchase_summary("1m", 0)
    assert isinstance(result, str)


def test_build_purchase_summary_3m():
    result = build_purchase_summary("3m", 5)
    assert result.total_price_rubles == 750
    assert result.plan_display_name == "3 месяца"


def test_build_purchase_summary_6m_extra():
    result = build_purchase_summary("6m", 7)
    assert result.total_price_rubles == 1350 + 2 * 80  # 1510
