"""Минимальные типобезопасные примитивы для slice 1 (UC-01 / UC-02)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OperationOutcomeCategory(StrEnum):
    """Категория результата верхнего уровня для структурной телеметрии (низкая кардинальность)."""

    SUCCESS = "success"
    VALIDATION_FAILURE = "validation_failure"
    IDEMPOTENT_NOOP = "idempotent_noop"
    NOT_FOUND = "not_found"
    RETRYABLE_DEPENDENCY = "retryable_dependency"
    INTERNAL_FAILURE = "internal_failure"


class SafeUserStatusCategory(StrEnum):
    """Категории пользовательского статуса с fail-closed для UC-02 (UC-05 может добавить явный subscription active)."""

    NEEDS_BOOTSTRAP = "needs_bootstrap"
    INACTIVE_OR_NOT_ELIGIBLE = "inactive_or_not_eligible"
    NEEDS_REVIEW = "needs_review"
    SUBSCRIPTION_EXPIRED = "subscription_expired"
    SUBSCRIPTION_ACTIVE = "subscription_active"
    SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY = "subscription_active_access_not_ready"
    SUBSCRIPTION_ACTIVE_ACCESS_READY = "subscription_active_access_ready"


class SubscriptionSnapshotState(StrEnum):
    """Классификация снапшота подписки (сохранённая или выведенная) для входных данных read model."""

    ABSENT = "absent"
    INACTIVE = "inactive"
    NOT_ELIGIBLE = "not_eligible"
    NEEDS_REVIEW = "needs_review"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class ActorContext:
    """Минимальный контекст актора для нормализованного входа (без сырых Telegram-нагрузок)."""

    telegram_user_id: int
    telegram_chat_id: int
