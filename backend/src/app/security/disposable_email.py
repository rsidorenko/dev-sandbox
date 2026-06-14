"""Disposable/temporary email detection for web registration.

Blocks known throwaway email domains (mailinator, temp-mail, 10minutemail,
guerrillamail, ...) at the web registration entry points. Uses a bundled,
periodically-refreshed denylist (``data/disposable_domains.txt`` — the public
`disposable-email-domains` project). Normal providers (gmail, yandex, mail.ru,
outlook, corporate domains, ...) are NOT in the list and are always allowed.

Design invariants:
  - DENYLIST semantics: everything is allowed by default; only a domain present
    in the bundled list (or a PARENT of the email domain) is blocked. Unknown /
    new / regional providers are therefore allowed.
  - FAIL-OPEN: on ANY error (missing/unreadable file, malformed email) the
    function returns ``False`` — a real user is never blocked by an infra hiccup.
  - PARENT-WALK: checks the domain and its parent levels so that
    ``user@sub.temp-mail.org`` is caught even if only ``temp-mail.org`` is listed.
  - ALLOWLIST OVERRIDE: env ``DISPOSABLE_EMAIL_ALLOW=a.com,b.com`` force-allows
    domains (emergency unblock of a legit provider without a release).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).resolve().parent / "data" / "disposable_domains.txt"
# Cap parent-walk depth so a pathological deeply-nested domain can't loop forever.
_MAX_PARENT_LEVELS = 5


@lru_cache(maxsize=1)
def _blocked_domains() -> frozenset[str]:
    """Load the disposable-domain denylist once (cached).

    Returns an empty set on any failure -> fail-open (nothing is blocked).
    """
    try:
        text = _DATA_FILE.read_text(encoding="utf-8")
    except OSError:
        _LOGGER.warning("disposable_email: blocklist not loaded (%s); failing open", _DATA_FILE)
        return frozenset()
    return frozenset(
        line.strip().lower()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    )


@lru_cache(maxsize=1)
def _allowlist() -> frozenset[str]:
    """Force-allowed domains from env ``DISPOSABLE_EMAIL_ALLOW=a.com,b.com``."""
    raw = os.environ.get("DISPOSABLE_EMAIL_ALLOW", "").strip()
    if not raw:
        return frozenset()
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _domain_of(email: str) -> str | None:
    """Lowercased, dot-stripped domain part of *email*, or ``None`` if it cannot
    be trusted (no single ``@`` split, no dot in the domain)."""
    if not isinstance(email, str):
        return None
    s = email.strip().lower()
    if s.count("@") != 1:
        return None
    _, _, domain = s.rpartition("@")
    domain = domain.strip(".")
    if not domain or "." not in domain:
        return None
    return domain


def is_disposable_email(email: str) -> bool:
    """True if *email*'s domain (or a parent domain) is in the disposable
    denylist.

    Always ``False`` for allowlisted domains and on any error (fail-open) so a
    real user is never blocked. Malformed addresses return ``False`` — the caller
    (:func:`app.web_api.helpers.validate_email`) is responsible for rejecting them.
    """
    try:
        domain = _domain_of(email)
        if domain is None:
            return False
        allow = _allowlist()
        blocked = _blocked_domains()
        # Walk the domain and its parents: foo.bar.temp-mail.org -> bar.temp-mail.org
        # -> temp-mail.org -> ... (so subdomains of a listed disposable are caught).
        parts = domain.split(".")
        for i in range(min(len(parts), _MAX_PARENT_LEVELS)):
            candidate = ".".join(parts[i:])
            if candidate in allow:
                return False
            if candidate in blocked:
                return True
        return False
    except Exception:  # noqa: BLE001 — fail-open: never block a real user
        _LOGGER.warning("disposable_email: check raised; failing open", exc_info=True)
        return False
