"""Помощники валидации публичных HTTPS-URL оператора."""

from __future__ import annotations

import ipaddress
from urllib.parse import parse_qsl, urlsplit

_TEST_HOST_SUFFIXES: tuple[str, ...] = (
    ".test.local",
    ".test",
    ".localhost",
    ".local",
    ".example",
    ".invalid",
)
_SUSPICIOUS_QUERY_NEEDLES: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "api_key",
    "key",
    "bearer",
    "signature",
    "credential",
)


def _contains_suspicious_fragment(value: str) -> bool:
    lowered = value.lower()
    return any(needle in lowered for needle in _SUSPICIOUS_QUERY_NEEDLES)


def _is_test_host(host: str) -> bool:
    lowered = host.lower()
    if lowered in {"localhost", "127.0.0.1", "::1"}:
        return True
    return any(lowered.endswith(suffix) for suffix in _TEST_HOST_SUFFIXES)


def _is_private_or_loopback_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def classify_public_https_url_host(raw_url: str | None) -> str:
    value = (raw_url or "").strip()
    if not value:
        return "missing"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "invalid"
    host = parsed.hostname
    if not host:
        return "invalid"
    lowered = host.lower()
    if lowered == "localhost":
        return "localhost"
    if _is_private_or_loopback_host(lowered):
        return "private"
    if _is_test_host(lowered):
        return "test"
    return "public"


def validate_public_https_operator_url(*, raw_url: str | None, allow_test_host: bool) -> str | None:
    value = (raw_url or "").strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https":
        return None
    if not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    if parsed.fragment:
        return None
    if parsed.query:
        for key, item in parse_qsl(parsed.query, keep_blank_values=True):
            if _contains_suspicious_fragment(key) or _contains_suspicious_fragment(item):
                return None

    host = parsed.hostname
    if not host:
        return None
    host_kind = classify_public_https_url_host(value)
    if host_kind in {"localhost", "private"}:
        return None
    if host_kind == "test" and not allow_test_host:
        return None
    if host_kind not in {"public", "test"}:
        return None
    return value
