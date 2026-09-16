"""Необязательные события воронки: ограниченные поля, короткий таймаут, без секретов."""

from __future__ import annotations

import asyncio
import logging
import os

from app.domain.plans import get_plan

_LOGGER = logging.getLogger(__name__)
_EVENTS = frozenset(
    {
        "setup_started",
        "platform_selected",
        "connection_confirmed",
        "connection_problem",
        "checkout_opened",
        "payment_created",
    }
)
_PLATFORMS = frozenset({"", "ios", "android", "mac", "win", "tv"})


def funnel_events_enabled() -> bool:
    return os.environ.get("FUNNEL_EVENTS_ENABLE", "").strip().lower() in ("1", "true", "yes", "on")


async def record_interaction(
    pool,
    *,
    event_name: str,
    surface: str,
    dedup_key: str,
    platform: str = "",
    internal_user_id: str | None = None,
    telegram_user_id: int | None = None,
    plan_id: str | None = None,
    device_count: int | None = None,
    payment_method: str | None = None,
) -> bool:
    """Сбой/отсутствие таблицы не меняет исход бизнес-операции; отмена задачи сохраняется."""
    if pool is None or not funnel_events_enabled():
        return False
    if (
        event_name not in _EVENTS
        or surface not in ("telegram", "web")
        or platform not in _PLATFORMS
        or not 1 <= len(dedup_key) <= 200
        or (plan_id is not None and get_plan(plan_id) is None)
        or (device_count is not None and not 5 <= device_count <= 20)
        or payment_method not in (None, "card", "crypto")
    ):
        return False
    try:
        async with asyncio.timeout(0.2):
            await pool.execute(
                "INSERT INTO funnel_interactions "
                "(internal_user_id, event_name, surface, platform, dedup_key, "
                "plan_id, device_count, payment_method) "
                "SELECT internal_user_id, $1, $2, $3, $4, $7, $8, $9 FROM user_identities "
                "WHERE internal_user_id = $5 OR ($5::text IS NULL AND telegram_user_id = $6) "
                "ON CONFLICT (surface, event_name, dedup_key) DO NOTHING",
                event_name,
                surface,
                platform,
                dedup_key,
                internal_user_id,
                telegram_user_id,
                plan_id,
                device_count,
                payment_method,
            )
        return True
    except Exception:
        # Не логируем payload/исключение: драйвер может включить параметры запроса.
        _LOGGER.warning("funnel_interaction_write_failed event=%s surface=%s", event_name, surface)
        return False


def callback_interaction(code: str) -> tuple[str, str] | None:
    """Маппинг существующих callback без изменения маршрутизации бота."""
    if code == "connect_device":
        return "setup_started", ""
    fast_problem_prefix = "connect_fast_problem:"
    if code.startswith(fast_problem_prefix):
        platform = code[len(fast_problem_prefix) :]
        if platform in ("ios", "android", "mac", "win"):
            return "connection_problem", platform
        return None
    for platform in ("ios", "android", "mac", "win", "tv"):
        if code in (f"connect_{platform}", f"connect_fast_{platform}"):
            return "platform_selected", platform
        if code == f"{platform}_yes":
            return "connection_confirmed", platform
        if code == f"{platform}_no":
            return "connection_problem", platform
    if code.startswith("confirm_pay:"):
        return "checkout_opened", ""
    return None


def checkout_context_from_callback(code: str) -> tuple[str, int] | None:
    """Извлекает валидный plan/device из callback сводки без обратного доверия строке."""
    prefix = "confirm_pay:"
    if not code.startswith(prefix):
        return None
    rest = code[len(prefix) :]
    sep = rest.rfind(":")
    if sep < 1:
        return None
    plan_id = rest[:sep]
    try:
        device_count = int(rest[sep + 1 :])
    except ValueError:
        return None
    if get_plan(plan_id) is None or not 5 <= device_count <= 20:
        return None
    return plan_id, device_count
