"""Единая проверка права на VPN-доступ для сайта, бота и подписки."""

from datetime import datetime


def entitlement_deadline(row, *, now: datetime) -> datetime | None:
    if row is None or row.get("state_label") != "active" or row.get("keys_deleted_at") is not None:
        return None
    deadlines = [row.get("trial_expires_at"), row.get("active_until_utc")]
    future = [value for value in deadlines if value is not None and value > now]
    return max(future) if future else None


def entitlement_active(row, *, now: datetime) -> bool:
    return entitlement_deadline(row, now=now) is not None
