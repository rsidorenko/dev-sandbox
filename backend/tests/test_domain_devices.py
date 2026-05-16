"""Tests for domain.devices: device limit validation and cost."""

from app.domain.devices import (
    extra_device_cost,
    extra_device_count,
    validate_device_count,
)


def test_validate_device_count_valid():
    assert validate_device_count(5) is None
    assert validate_device_count(10) is None
    assert validate_device_count(20) is None


def test_validate_device_count_too_low():
    assert validate_device_count(4) is not None
    assert validate_device_count(1) is not None
    assert validate_device_count(0) is not None
    assert validate_device_count(-1) is not None


def test_validate_device_count_too_high():
    assert validate_device_count(21) is not None
    assert validate_device_count(100) is not None


def test_extra_device_count_default():
    assert extra_device_count(5) == 0
    assert extra_device_count(3) == 0
    assert extra_device_count(7) == 2
    assert extra_device_count(10) == 5


def test_extra_device_cost_default():
    assert extra_device_cost(5) == 0
    assert extra_device_cost(7) == 160  # 2 * 80
    assert extra_device_cost(10) == 400  # 5 * 80


def test_extra_device_cost_1_extra():
    assert extra_device_cost(6) == 80
