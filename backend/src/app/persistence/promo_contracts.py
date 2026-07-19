"""Promo code persistence contracts (protocols, no concrete DB)."""

from __future__ import annotations

from typing import Protocol

from app.domain.promo import PromoCode


class PromoCodeRepository(Protocol):
    async def get_by_code(self, code: str) -> PromoCode | None: ...

    async def create(self, record: PromoCode) -> PromoCode:
        """Insert a new code. Caller chooses the code string (ensures uniqueness via PK)."""
        ...

    async def consume(self, code: str) -> bool:
        """Atomically increment uses_count for a currently-usable code.
        Returns True if consumed (uses_count < max_uses and active/in-window), False otherwise."""
        ...

    async def ensure_personal_code(
        self,
        *,
        owner_internal_user_id: str,
        code: str,
        discount_percent: int,
        valid_days: int,
        max_uses: int = 1,
    ) -> PromoCode:
        """Idempotently create a personal promo code for an owner (e.g. on trial-eve).
        Returns the existing record if the code already exists."""
        ...
