"""Резолв A/B-варианта гейта подписки на канал (trial_channel_gate_v1).

Экспозиция = момент, когда пользователь дошёл до активации триала при включённом
жёстком гейте. Назначение детерминировано (domain.assign_experiment_variant) и
пишется в experiment_assignments идемпотентно. Любой сбой — fail-open в control:
эксперимент не должен менять политику выдачи при технической неопределённости.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.domain.experiments import (
    TRIAL_CHANNEL_GATE_EXPERIMENT,
    assign_experiment_variant,
)

_LOGGER = logging.getLogger(__name__)

CONTROL_VARIANT = "control"
SOFT_VARIANT = "soft"


def experiment_channel_gate_enabled() -> bool:
    return os.environ.get("EXPERIMENT_CHANNEL_GATE_ENABLE", "").strip().lower() in ("1", "true", "yes", "on")


async def resolve_trial_gate_variant(pool: Any, internal_user_id: str | None) -> str:
    """Вариант для пользователя; 'control' при выключенном эксперименте/сбое."""
    if not experiment_channel_gate_enabled() or pool is None or not internal_user_id:
        return CONTROL_VARIANT
    try:
        variant = assign_experiment_variant(
            internal_user_id=internal_user_id,
            experiment_key=TRIAL_CHANNEL_GATE_EXPERIMENT,
        )
    except ValueError:
        return CONTROL_VARIANT
    try:
        await pool.execute(
            "INSERT INTO experiment_assignments (internal_user_id, experiment_key, variant) "
            "VALUES ($1, $2, $3) ON CONFLICT (internal_user_id, experiment_key) DO NOTHING",
            internal_user_id,
            TRIAL_CHANNEL_GATE_EXPERIMENT,
            variant,
        )
    except Exception:
        # Назначение — аналитика; сбой записи не должен менять пользовательский flow.
        _LOGGER.warning("trial_gate_experiment.assignment_write_failed")
    return variant
