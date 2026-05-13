"""ADM-02 identity resolve adapter aliasing existing ADM-01 resolver implementation."""

from __future__ import annotations

from app.admin_support.adm01_identity_resolve_adapter import Adm01IdentityResolveAdapter


class Adm02IdentityResolveAdapter(Adm01IdentityResolveAdapter):
    """ADM-02 reuses the same safe identity resolution semantics as ADM-01."""
