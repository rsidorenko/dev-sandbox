"""Device limit logic for subscriptions."""

from __future__ import annotations

from app.domain.plans import DEFAULT_DEVICE_LIMIT, EXTRA_DEVICE_PRICE_RUBLES

MAX_DEVICE_COUNT = 20


def validate_device_count(count: int) -> str | None:
    if count < DEFAULT_DEVICE_LIMIT:
        return f"Минимум {DEFAULT_DEVICE_LIMIT} устройств"
    if count > MAX_DEVICE_COUNT:
        return f"Максимум {MAX_DEVICE_COUNT} устройств"
    return None


def extra_device_count(requested: int, default: int = DEFAULT_DEVICE_LIMIT) -> int:
    return max(0, requested - default)


def extra_device_cost(requested: int, default: int = DEFAULT_DEVICE_LIMIT, duration_days: int = 30) -> int:
    extra = extra_device_count(requested, default)
    if extra == 0:
        return 0
    daily_price = EXTRA_DEVICE_PRICE_RUBLES / 30
    return extra * round(daily_price * duration_days)
