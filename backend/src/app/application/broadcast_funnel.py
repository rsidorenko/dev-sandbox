"""Явный источник быстрого бонусного checkout; аналитика не блокирует покупку."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

PREFIX = "bp:"
_FIXED_PLANS = frozenset({"1d", "7d", "14d", "1m", "3m", "6m", "365d"})
_LOGGER = logging.getLogger(__name__)


def payment_callback(job_id: str, plan_id: str, devices: int) -> str:
    if plan_id not in _FIXED_PLANS or not 5 <= devices <= 20:
        raise ValueError("invalid_broadcast_checkout")
    return f"{PREFIX}{UUID(job_id).hex}:{plan_id}:{devices}"


def parse_payment_callback(value: str) -> tuple[str, str, int] | None:
    try:
        prefix, raw_job, plan, raw_devices = value.split(":")
        if prefix != "bp" or plan not in _FIXED_PLANS:
            return None
        devices = int(raw_devices)
        if not 5 <= devices <= 20:
            return None
        return str(UUID(raw_job)), plan, devices
    except (ValueError, TypeError, AttributeError):
        return None


def checkout_dedup_key(job_id: str, internal_user_id: str, plan_id: str, devices: int) -> str:
    return f"broadcast:{job_id}:{internal_user_id}:{plan_id}:{devices}"


async def record_bonus_event(
    pool, *, job_id: str, internal_user_id: str, stage: str, payment_id: str | None = None
) -> None:
    if pool is None:
        return
    try:
        async with asyncio.timeout(0.2):
            await pool.execute(
                "INSERT INTO broadcast_bonus_funnel (job_id,internal_user_id,stage,event_key,payment_id) "
                "VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
                job_id,
                internal_user_id,
                stage,
                payment_id or stage,
                payment_id,
            )
    except Exception:
        _LOGGER.warning("broadcast_funnel_record_failed stage=%s", stage)
