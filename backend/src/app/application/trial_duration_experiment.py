"""Резолв A/B-варианта длительности триала (trial_duration_v1).

Экспозиция — активация триала. Назначение детерминировано и пишется в
experiment_assignments идемпотентно. Любой сбой — fail-open в контрольные 3 дня:
эксперимент не должен менять окно триала при технической неопределённости.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.domain.experiments import (
    TRIAL_DURATION_EXPERIMENT,
    assign_experiment_variant,
)
from app.domain.trial import TRIAL_DURATION_DAYS

_LOGGER = logging.getLogger(__name__)

# Единственная точка правды о варианто-днях; SQL в postgres_traffic_stats
# содержит эквивалентный CASE (синхронность фиксирует тест-контракт).
TRIAL_DURATION_VARIANTS_DAYS: dict[str, int] = {
    "control": TRIAL_DURATION_DAYS,
    "7d": 7,
}


def experiment_trial_duration_enabled() -> bool:
    return os.environ.get("EXPERIMENT_TRIAL_DURATION_ENABLE", "").strip().lower() in ("1", "true", "yes", "on")


def trial_days_for_variant(variant: str) -> int:
    return TRIAL_DURATION_VARIANTS_DAYS.get(variant, TRIAL_DURATION_DAYS)


async def resolve_trial_duration_days(pool: Any, internal_user_id: str | None) -> int:
    """Длительность триала пользователя в днях; 3 при выключенном эксперименте/сбое."""
    if not experiment_trial_duration_enabled() or pool is None or not internal_user_id:
        return TRIAL_DURATION_DAYS
    try:
        variant = assign_experiment_variant(
            internal_user_id=internal_user_id,
            experiment_key=TRIAL_DURATION_EXPERIMENT,
        )
    except ValueError:
        return TRIAL_DURATION_DAYS
    try:
        persisted_variant = await pool.fetchval(
            """WITH inserted AS (
                   INSERT INTO experiment_assignments (internal_user_id, experiment_key, variant)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (internal_user_id, experiment_key) DO NOTHING
                   RETURNING variant
               )
               SELECT variant FROM inserted
               UNION ALL
               SELECT variant FROM experiment_assignments
               WHERE internal_user_id = $1 AND experiment_key = $2
               LIMIT 1""",
            internal_user_id,
            TRIAL_DURATION_EXPERIMENT,
            variant,
        )
    except Exception:
        # Без сохранённого назначения нельзя безопасно выдать альтернативный срок:
        # scheduler первого трафика читает тот же persisted variant.
        _LOGGER.warning("trial_duration_experiment.assignment_write_failed")
        return TRIAL_DURATION_DAYS
    return trial_days_for_variant(str(persisted_variant))
