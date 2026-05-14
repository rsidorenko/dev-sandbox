"""Единая конфигурация логирования для всего приложения (JSON для production, человекочитаемый для dev)."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime


class JSONFormatter(logging.Formatter):
    """Форматтер, выдающий одну JSON-строку на запись лога."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            obj["exc_type"] = record.exc_info[0].__name__
        if hasattr(record, "structured_fields"):
            fields = record.structured_fields
            if isinstance(fields, dict):
                obj["fields"] = fields
        return json.dumps(obj, default=str, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Человекочитаемый форматтер для dev/local окружения."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        base = f"{ts} {record.levelname:5s} [{record.name}] {record.getMessage()}"
        if hasattr(record, "structured_fields"):
            fields = record.structured_fields
            if isinstance(fields, dict):
                base += f" {fields}"
        return base


def configure_logging() -> None:
    """Настраивает корневой логгер: JSON в production, человекочитаемый в dev."""
    env = os.environ.get("APP_ENV", "").strip().lower()
    is_production = env in ("production", "prod", "staging", "stage")

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter() if is_production else HumanFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
