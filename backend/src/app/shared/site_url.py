"""Shared site URL helper — reads SUBSCRIPTION_BASE_URL / NEXT_PUBLIC_SITE_URL from env."""

from __future__ import annotations

import os


def get_site_base_url() -> str:
    """Return the configured public site base URL (no trailing slash).

    Resolution order:
      1. SUBSCRIPTION_BASE_URL env var
      2. NEXT_PUBLIC_SITE_URL env var
      3. Hardcoded fallback ``https://bravada-connect.ru``
    """
    return (
        os.environ.get("SUBSCRIPTION_BASE_URL", "").strip()
        or os.environ.get("NEXT_PUBLIC_SITE_URL", "").strip()
        or "https://bravada-connect.ru"
    ).rstrip("/")
