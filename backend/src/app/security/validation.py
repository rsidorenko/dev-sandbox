"""Строгие разрешённые интенты для slice 1; ограниченная валидация; неизвестные интенты запрещены."""

from __future__ import annotations

import re
from enum import StrEnum

_MAX_INTENT_STRING_LEN = 64
_MAX_INTERNAL_FACT_REF_LEN = 256
_UC05_REF_SAFE = re.compile(r"^[\w.\-:]{1,256}$")


class NormalizedIntent(StrEnum):
    """Только интенты, разрешённые для нормализации транспорта slice 1."""

    BOOTSTRAP_IDENTITY = "bootstrap_identity"
    GET_SUBSCRIPTION_STATUS = "get_subscription_status"


class ValidationError(Exception):
    """Выбрасывается, когда нормализованный ввод не проходит проверку границ или разрешённого списка."""


def parse_allowlisted_intent(raw: str | None) -> NormalizedIntent:
    """
    Разбирает интент из нормализованной строки. Отклоняет неизвестные интенты и слишком длинный ввод.
    """
    if raw is None:
        raise ValidationError("intent is required")
    if not isinstance(raw, str):
        raise ValidationError("intent must be a string")
    s = raw.strip()
    if not s:
        raise ValidationError("intent is empty")
    if len(s) > _MAX_INTENT_STRING_LEN:
        raise ValidationError("intent exceeds maximum length")
    try:
        return NormalizedIntent(s)
    except ValueError:
        raise ValidationError("unknown intent") from None


def validate_telegram_user_id(value: int) -> int:
    """Ограниченный положительный Telegram user id."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError("telegram_user_id must be an integer")
    if value <= 0:
        raise ValidationError("telegram_user_id out of bounds")
    if value > 2**63 - 1:
        raise ValidationError("telegram_user_id out of bounds")
    return value


def validate_telegram_update_id(value: int) -> int:
    """Неотрицательный update id для материала идемпотентности (Telegram update_id)."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError("update_id must be an integer")
    if value < 0:
        raise ValidationError("update_id out of bounds")
    if value > 2**63 - 1:
        raise ValidationError("update_id out of bounds")
    return value


def validate_internal_fact_ref_uc05(value: str) -> str:
    """Ограниченный :class:`internal_fact_ref` для UC-05 (согласован с правилами billing ingest ref)."""
    if not isinstance(value, str):
        raise ValidationError("internal_fact_ref must be a string")
    s = value.strip()
    if not s:
        raise ValidationError("internal_fact_ref is required")
    if len(s) > _MAX_INTERNAL_FACT_REF_LEN:
        raise ValidationError("internal_fact_ref exceeds maximum length")
    if _UC05_REF_SAFE.fullmatch(s) is None:
        raise ValidationError("internal_fact_ref has invalid format")
    return s
