"""Subscription renewal timing — single source of truth for ``active_until``.

Used by both the card-payment path (``process_fulfillment``) and the balance-payment
service, so a plan purchased either way yields the same ``active_until`` (Phase 3 L10).
"""

from __future__ import annotations

from datetime import datetime, timedelta


def compute_renewal_active_until(
    base: datetime,
    duration_days: int,
    normalization: str = "wall_clock",
) -> datetime:
    """Compute the new ``active_until`` for a renewal/activation.

    ``base`` is the anchor (``max(now, existing.active_until)`` for a renewal, or
    ``paid_at`` for a fresh activation). ``duration_days`` extends from it.

    ``normalization``:
      * ``"wall_clock"`` (default, Phase 3 decision) — keeps the wall-clock instant
        (``base + duration_days`` to the microsecond). Card-path historical behaviour.
      * ``"midnight"`` — rounds DOWN to local midnight (00:00:00). Balance-path
        historical behaviour. Kept available for callers that explicitly want it.
    """
    extended = base + timedelta(days=duration_days)
    if normalization == "midnight":
        return extended.replace(hour=0, minute=0, second=0, microsecond=0)
    return extended
