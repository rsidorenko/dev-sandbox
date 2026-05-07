"""Безопасная таксономия ошибок: пользовательские vs внутренние, удобное отображение fail-closed."""

from __future__ import annotations

from enum import Enum


class UserSafeErrorCode(str, Enum):
    """Стабильные категории, безопасные для отображения конечным пользователям (без внутренних деталей)."""

    INVALID_INPUT = "invalid_input"
    TRY_AGAIN_LATER = "try_again_later"
    NOT_REGISTERED = "not_registered"
    SERVICE_UNAVAILABLE = "service_unavailable"


class InternalErrorCategory(str, Enum):
    """Операционная классификация ошибок (не для конечных пользователей)."""

    VALIDATION = "validation"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    PERSISTENCE_TRANSIENT = "persistence_transient"
    PERSISTENCE_INVARIANT = "persistence_invariant"
    UNKNOWN = "unknown"


class PersistenceDependencyError(Exception):
    """Выбрасывается реализациями репозиториев при ошибке персистентности; содержит внутреннюю классификацию."""

    def __init__(self, category: InternalErrorCategory) -> None:
        self.category = category
        super().__init__(category.value)


def map_internal_to_user_safe(category: InternalErrorCategory) -> UserSafeErrorCode:
    """Отображение fail-closed из внутренних категорий в пользовательские коды."""
    if category is InternalErrorCategory.VALIDATION:
        return UserSafeErrorCode.INVALID_INPUT
    if category is InternalErrorCategory.PERSISTENCE_TRANSIENT:
        return UserSafeErrorCode.TRY_AGAIN_LATER
    if category is InternalErrorCategory.IDEMPOTENCY_CONFLICT:
        return UserSafeErrorCode.TRY_AGAIN_LATER
    if category is InternalErrorCategory.PERSISTENCE_INVARIANT:
        return UserSafeErrorCode.SERVICE_UNAVAILABLE
    return UserSafeErrorCode.SERVICE_UNAVAILABLE
