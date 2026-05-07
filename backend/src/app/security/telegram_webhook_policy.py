"""Общая политика URL и allowed_updates для вебхуков Telegram (инструменты оператора)."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

_ALLOWED_UPDATE_RE = "abcdefghijklmnopqrstuvwxyz_"
_DEFAULT_ALLOWED_UPDATES: tuple[str, ...] = ("message",)
_COMMAND_BOT_SUPPORTED_UPDATE_TYPES = frozenset({"message"})


def parse_webhook_allowed_updates(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return _DEFAULT_ALLOWED_UPDATES
    parts = [part.strip() for part in raw.split(",")]
    normalized = tuple(part for part in parts if part)
    if not normalized:
        return _DEFAULT_ALLOWED_UPDATES
    for item in normalized:
        if not item or any(ch not in _ALLOWED_UPDATE_RE for ch in item.lower()):
            raise ValueError("invalid_allowed_updates")
    return normalized


def validate_allowed_updates_for_command_bot(updates: tuple[str, ...]) -> str | None:
    """Возвращает issue_code если тип не поддерживается для command-only бота, иначе None."""
    unsupported = sorted({u for u in updates if u not in _COMMAND_BOT_SUPPORTED_UPDATE_TYPES})
    if unsupported:
        return "telegram_webhook_allowed_updates_unsupported_for_command_bot"
    return None


def normalize_webhook_url_for_compare(url: str) -> str:
    """Нормализует публичный URL вебхука для сравнения на равенство (без query/fragment)."""
    stripped = url.strip()
    parsed = urlsplit(stripped)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = (parsed.path or "").rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))
