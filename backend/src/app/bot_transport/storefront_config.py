"""Storefront v1 public config parsing and safe rendering helpers."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SUSPICIOUS_NEEDLES: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "api_key",
    "key",
    "bearer",
    "signature",
    "dsn",
)
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9_]{3,64}$")


@dataclass(frozen=True, slots=True)
class StorefrontPublicConfig:
    plan_name: str | None
    plan_price: str | None
    checkout_url: str | None
    renewal_url: str | None
    support_url: str | None
    support_handle: str | None


def _norm_text(v: str | None) -> str | None:
    if v is None:
        return None
    t = v.strip()
    return t or None


def _contains_suspicious_fragment(value: str) -> bool:
    lowered = value.lower()
    return any(needle in lowered for needle in _SUSPICIOUS_NEEDLES)


def _validate_public_https_url(raw: str | None) -> str | None:
    value = _norm_text(raw)
    if value is None:
        return None
    if _contains_suspicious_fragment(value):
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
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        if _contains_suspicious_fragment(key) or _contains_suspicious_fragment(val):
            return None
    return value


def validate_storefront_public_https_url(raw: str | None) -> str | None:
    """Public URL validator for storefront-facing links (strict https-only)."""
    return _validate_public_https_url(raw)


def load_checkout_reference_secret(getenv: Callable[[str], str | None] | None = None) -> str | None:
    read = getenv or os.environ.get
    value = _norm_text(read("TELEGRAM_CHECKOUT_REFERENCE_SECRET"))
    return value


def build_checkout_url_with_reference(
    *,
    base_url: str,
    client_reference_id: str,
    client_reference_proof: str,
) -> str | None:
    """Append safe signed customer reference params to validated checkout URL."""
    if _validate_public_https_url(base_url) is None:
        return None
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        return None
    pairs = list(parse_qsl(parsed.query, keep_blank_values=True))
    existing_keys = {k.lower() for k, _ in pairs}
    # Fail closed if existing query already has suspicious names/fragments.
    if any(_contains_suspicious_fragment(key) for key in existing_keys):
        return None
    if "client_reference_id" in existing_keys or "client_reference_proof" in existing_keys:
        return None
    pairs.append(("client_reference_id", client_reference_id))
    pairs.append(("client_reference_proof", client_reference_proof))
    query = urlencode(pairs, doseq=True, safe=":/-._~")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _validate_support_handle(raw: str | None) -> str | None:
    value = _norm_text(raw)
    if value is None:
        return None
    if not _HANDLE_RE.match(value):
        return None
    return value


def load_storefront_public_config(
    getenv: Callable[[str], str | None] | None = None,
) -> StorefrontPublicConfig:
    read = getenv or os.environ.get
    plan_name = _norm_text(read("TELEGRAM_STOREFRONT_PLAN_NAME"))
    plan_price = _norm_text(read("TELEGRAM_STOREFRONT_PLAN_PRICE"))
    checkout_url = validate_storefront_public_https_url(read("TELEGRAM_STOREFRONT_CHECKOUT_URL"))
    renewal_url = validate_storefront_public_https_url(read("TELEGRAM_STOREFRONT_RENEWAL_URL"))
    support_url = validate_storefront_public_https_url(read("TELEGRAM_STOREFRONT_SUPPORT_URL"))
    support_handle = _validate_support_handle(read("TELEGRAM_STOREFRONT_SUPPORT_HANDLE"))
    return StorefrontPublicConfig(
        plan_name=plan_name,
        plan_price=plan_price,
        checkout_url=checkout_url,
        renewal_url=renewal_url,
        support_url=support_url,
        support_handle=support_handle,
    )
