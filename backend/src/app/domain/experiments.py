"""Строгий реестр продуктовых экспериментов и стабильное назначение вариантов."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExperimentDefinition:
    key: str
    variants: tuple[str, ...]


CHECKOUT_REASSURANCE_EXPERIMENT = "checkout_reassurance_v1"
TRIAL_CHANNEL_GATE_EXPERIMENT = "trial_channel_gate_v1"
TRIAL_DURATION_EXPERIMENT = "trial_duration_v1"

_EXPERIMENTS: dict[str, ExperimentDefinition] = {
    CHECKOUT_REASSURANCE_EXPERIMENT: ExperimentDefinition(
        key=CHECKOUT_REASSURANCE_EXPERIMENT,
        variants=("control", "reassurance"),
    ),
    # control — жёсткий гейт подписки на канал до триала (текущее поведение);
    # soft — триал сразу, предложение подписки после активации (не блокирует).
    TRIAL_CHANNEL_GATE_EXPERIMENT: ExperimentDefinition(
        key=TRIAL_CHANNEL_GATE_EXPERIMENT,
        variants=("control", "soft"),
    ),
    # control — триал 3 дня (текущее поведение); 7d — 7 дней. Цены не меняются.
    TRIAL_DURATION_EXPERIMENT: ExperimentDefinition(
        key=TRIAL_DURATION_EXPERIMENT,
        variants=("control", "7d"),
    ),
}


def get_experiment(experiment_key: str) -> ExperimentDefinition | None:
    return _EXPERIMENTS.get(experiment_key)


def assign_experiment_variant(*, internal_user_id: str, experiment_key: str) -> str:
    """Равномерный детерминированный bucket; неизвестный ключ отклоняется."""
    experiment = get_experiment(experiment_key)
    if experiment is None:
        raise ValueError("unknown_experiment")
    digest = hashlib.sha256(f"{experiment_key}:{internal_user_id}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big")
    return experiment.variants[bucket % len(experiment.variants)]
