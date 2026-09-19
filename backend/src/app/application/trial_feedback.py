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
_PLATFORMS = frozenset({"ios", "android", "mac", "win", "tv"})
_ROUTES = frozenset({"direct", "guided"})


def _valid_context(*, feedback_type: str, reason_code: str, platform: str, connection_route: str) -> bool:
    if feedback_type == "connection_problem":
        return reason_code in CONNECTION_PROBLEM_REASONS and platform in _PLATFORMS and connection_route in _ROUTES
    if feedback_type == "trial_nonpayment":
        return reason_code in TRIAL_NONPAYMENT_REASONS and platform == "" and connection_route == ""
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
) -> bool:
    """Upsert feedback; сбой аналитики никогда не меняет пользовательский flow."""
    if pool is None or (telegram_user_id is None and internal_user_id is None):
        return False
    if not _valid_context(
        feedback_type=feedback_type,
        reason_code=reason_code,
        platform=platform,
        connection_route=connection_route,
    ):
        return False
    try:
        await pool.execute(
            """INSERT INTO trial_feedback
               (internal_user_id, feedback_type, reason_code, platform, connection_route)
               SELECT internal_user_id, $1, $2, $3, $4
               FROM user_identities
               WHERE internal_user_id = $5 OR ($5::text IS NULL AND telegram_user_id = $6)
               ON CONFLICT (internal_user_id, feedback_type, platform) DO UPDATE
               SET reason_code = EXCLUDED.reason_code,
                   connection_route = EXCLUDED.connection_route,
                   updated_at = now()""",
            feedback_type,
            reason_code,
            platform,
            connection_route,
            internal_user_id,
            telegram_user_id,
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.warning("trial_feedback.write_failed type=%s", feedback_type, exc_info=True)
        return False
