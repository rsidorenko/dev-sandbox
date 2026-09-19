"""Безопасная запись закрытых причин отвалов в trial flow."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

CONNECTION_PROBLEM_REASONS = frozenset(
    {"install_failed", "import_failed", "connect_failed", "traffic_failed", "too_hard", "other"}
)
TRIAL_NONPAYMENT_REASONS = frozenset({"too_expensive", "did_not_work", "too_hard", "no_need", "payment_issue", "other"})
_PLATFORMS = frozenset({"", "ios", "android", "mac", "win", "tv"})  # '' — web-страница /check при неопознанном UA
_ROUTES = frozenset({"direct", "guided", ""})  # '' — web-страница /check не знает bot-маршрут
_SURFACES = frozenset({"telegram", "web"})


def _valid_context(
    *,
    feedback_type: str,
    reason_code: str,
    platform: str,
    connection_route: str,
    surface: str,
) -> bool:
    if surface not in _SURFACES:
        return False
    if feedback_type == "connection_problem":
        if reason_code not in CONNECTION_PROBLEM_REASONS or platform not in _PLATFORMS:
            return False
        if surface == "web":
            return connection_route == ""
        return platform != "" and connection_route in _ROUTES - {""}
    if feedback_type == "trial_nonpayment":
        return (
            surface == "telegram"
            and reason_code in TRIAL_NONPAYMENT_REASONS
            and platform == ""
            and connection_route == ""
        )
    return False


async def record_trial_feedback(
    pool: Any | None,
    *,
    feedback_type: str,
    reason_code: str,
    telegram_user_id: int | None = None,
    internal_user_id: str | None = None,
    platform: str = "",
    connection_route: str = "",
    surface: str = "telegram",
) -> bool:
    """Upsert feedback внутри surface; сбой аналитики не меняет flow."""
    if pool is None or (telegram_user_id is None and internal_user_id is None):
        return False
    if not _valid_context(
        feedback_type=feedback_type,
        reason_code=reason_code,
        platform=platform,
        connection_route=connection_route,
        surface=surface,
    ):
        return False
    try:
        await pool.execute(
            """INSERT INTO trial_feedback
               (internal_user_id, feedback_type, reason_code, platform, connection_route, surface)
               SELECT internal_user_id, $1, $2, $3, $4, $5
               FROM user_identities
               WHERE internal_user_id = $6 OR ($6::text IS NULL AND telegram_user_id = $7)
               ON CONFLICT (internal_user_id, feedback_type, platform, surface) DO UPDATE
               SET reason_code = EXCLUDED.reason_code,
                   connection_route = EXCLUDED.connection_route,
                   updated_at = now()""",
            feedback_type,
            reason_code,
            platform,
            connection_route,
            surface,
            internal_user_id,
            telegram_user_id,
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.warning("trial_feedback.write_failed type=%s", feedback_type, exc_info=True)
        return False
