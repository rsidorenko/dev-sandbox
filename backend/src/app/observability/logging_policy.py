"""Структурированный разрешённый список полей / редукция; тестируемый без внешних logging-бэкендов."""

from __future__ import annotations

import re
from typing import Any

# Slice 1: только низкокардинальные операционные поля (без свободного текста / нагрузок).
ALLOWED_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "correlation_id",
        "intent",
        "operation",
        "outcome",
        "error_code",
        "internal_category",
    }
)

_REDACT_SUBSTRINGS = (
    "token",
    "secret",
    "password",
    "authorization",
    "bearer",
    "message_text",
    "raw",
    "payload",
)


def _looks_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(s in lower for s in _REDACT_SUBSTRINGS)


_CORRELATION_RE = re.compile(r"^[0-9a-f]{32}$")


def sanitize_structured_fields(record: dict[str, Any]) -> dict[str, Any]:
    """
    Возвращает новый словарь только с разрешёнными ключами; значения редуцируются по необходимости.

    - Отбрасывает неизвестные ключи.
    - Редуцирует значения для ключей, которые выглядят чувствительными.
    - Проверяет формат correlation_id при наличии.
    """
    out: dict[str, Any] = {}
    for key, value in record.items():
        if key not in ALLOWED_LOG_FIELDS:
            continue
        if _looks_sensitive_key(key):
            out[key] = "[REDACTED]"
            continue
        if key == "correlation_id":
            if not isinstance(value, str) or _CORRELATION_RE.match(value) is None:
                out[key] = "[INVALID]"
            else:
                out[key] = value
            continue
        if isinstance(value, str | int | float | bool) or value is None:
            out[key] = value
        else:
            out[key] = "[REDACTED]"
    return out
