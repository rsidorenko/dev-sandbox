"""Конфигурация времени выполнения, загружаемая из окружения (секреты никогда не логируются здесь)."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(Exception):
    """Выбрасывается, когда обязательная конфигурация отсутствует или некорректна."""


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Конфигурация slice-1 runtime (единая граница для секретов и настроек сервиса)."""

    bot_token: str
    database_url: str | None
    app_env: str
    debug_safe: bool


def _require_non_empty(name: str) -> str:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise ConfigurationError(f"missing or empty configuration: {name}")
    return raw


def _is_local_env(app_env: str) -> bool:
    return app_env.strip().lower() in {"development", "dev", "local", "test"}


def _has_explicit_sslmode(database_url: str) -> bool:
    return "sslmode=" in database_url.lower()


def validate_runtime_config(config: RuntimeConfig) -> None:
    """
    Валидирует уже собранный :class:`RuntimeConfig` (без чтения окружения).

    Применяет те же правила, что и :func:`load_runtime_config` для токена, формата DSN
    и политики ``sslmode`` для non-local PostgreSQL. Никогда не логирует сырые значения DSN.
    """
    if len(config.bot_token) < 10:
        raise ConfigurationError("invalid configuration: BOT_TOKEN")

    database_url = config.database_url
    if database_url and database_url.strip():
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ConfigurationError("invalid configuration: DATABASE_URL")
        if not _is_local_env(config.app_env) and not _has_explicit_sslmode(database_url):
            raise ConfigurationError("invalid configuration: DATABASE_URL")


def load_runtime_config() -> RuntimeConfig:
    """
    Загружает конфигурацию из окружения процесса.

    Никогда не логирует значения. При ошибке выбрасывает ConfigurationError только с именами полей.
    """
    bot_token = _require_non_empty("BOT_TOKEN")

    app_env = os.environ.get("APP_ENV", "development").strip() or "development"
    database_raw = os.environ.get("DATABASE_URL", "").strip()
    database_url: str | None = database_raw if database_raw else None

    debug_raw = os.environ.get("DEBUG", "").strip().lower()
    debug_safe = debug_raw in ("1", "true", "yes")

    config = RuntimeConfig(
        bot_token=bot_token,
        database_url=database_url,
        app_env=app_env,
        debug_safe=debug_safe,
    )
    validate_runtime_config(config)
    return config
