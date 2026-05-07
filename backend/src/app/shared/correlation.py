"""Генерация и валидация correlation id (только stdlib)."""

from __future__ import annotations

import re
import secrets

_CORRELATION_HEX_LEN = 32
_PATTERN = re.compile(rf"^[0-9a-f]{{{_CORRELATION_HEX_LEN}}}$")


def new_correlation_id() -> str:
    """Возвращает новый криптографически стойкий correlation id (hex, фиксированная длина)."""
    return secrets.token_hex(_CORRELATION_HEX_LEN // 2)


def is_valid_correlation_id(value: str) -> bool:
    """Возвращает True, если значение — непустой, ограниченный, lowercase hex correlation id."""
    if not isinstance(value, str):
        return False
    if len(value) != _CORRELATION_HEX_LEN:
        return False
    return _PATTERN.match(value) is not None


def require_correlation_id(value: str) -> str:
    """Валидирует и возвращает correlation id или выбрасывает ValueError."""
    if not is_valid_correlation_id(value):
        raise ValueError("invalid correlation id")
    return value
