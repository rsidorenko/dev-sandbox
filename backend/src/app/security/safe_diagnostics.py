"""Безопасные помощники диагностики для предзапускового вывода оператору."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

_SUSPICIOUS_NEEDLES: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "api_key",
    "key",
    "bearer",
    "dsn",
    "signature",
    "credential",
)


def _contains_suspicious_fragment(value: str) -> bool:
    lowered = value.lower()
    return any(needle in lowered for needle in _SUSPICIOUS_NEEDLES)


def has_suspicious_query_pattern(raw_url: str | None) -> bool:
    value = (raw_url or "").strip()
    if not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return True
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if _contains_suspicious_fragment(key) or _contains_suspicious_fragment(item):
            return True
    return False


def redact_url_for_diagnostics(raw_url: str | None) -> str:
    value = (raw_url or "").strip()
    if not value:
        return "<missing>"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    if not parsed.scheme or not parsed.netloc:
        return "<invalid-url>"
    return f"{parsed.scheme.lower()}://{parsed.netloc}/<redacted>"


def redact_dsn_for_diagnostics(raw_dsn: str | None) -> str:
    value = (raw_dsn or "").strip()
    if not value:
        return "<missing>"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-dsn>"
    scheme = parsed.scheme.lower()
    host = parsed.hostname or "<no-host>"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    if scheme not in ("postgresql", "postgres"):
        return f"{scheme or '<unknown>'}://{host}/<redacted>"
    return f"postgresql://{host}/<redacted>"
