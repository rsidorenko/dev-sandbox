"""Tests for domain.promo + InMemoryPromoCodeRepository (discount math, validation, consume)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.promo import (
    MIN_PAYMENT_RUBLES,
    PromoCode,
    PromoValidationError,
    calculate_discounted_rubles,
    validate_promo,
)
from app.persistence.in_memory import InMemoryPromoCodeRepository
from app.shared.test_helpers import run_async as _run


def _now() -> datetime:
    return datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def _promo(
    *,
    code: str = "BRAVADA-ABC12345",
    active: bool = True,
    valid_until_offset_hours: float | None = 24.0,
    uses_count: int = 0,
    max_uses: int = 1,
    discount_percent: int = 15,
) -> PromoCode:
    now = _now()
    valid_until = now + timedelta(hours=valid_until_offset_hours) if valid_until_offset_hours is not None else None
    return PromoCode(
        code=code,
        owner_internal_user_id="u1",
        discount_percent=discount_percent,
        valid_from=now - timedelta(hours=1),
        valid_until=valid_until,
        max_uses=max_uses,
        uses_count=uses_count,
        active=active,
    )


# ── discount math ───────────────────────────────────────────────────────────────


def test_calculate_discounted_rubles_percent() -> None:
    assert calculate_discounted_rubles(249, 15) == 212  # round(249 * 0.85) = 211.65 → 212
    assert calculate_discounted_rubles(699, 15) == 594  # round(699 * 0.85) = 594.15 → 594
    assert calculate_discounted_rubles(100, 50) == 50


def test_calculate_discounted_rubles_zero_or_negative_is_noop() -> None:
    assert calculate_discounted_rubles(249, 0) == 249
    assert calculate_discounted_rubles(249, -5) == 249


def test_calculate_discounted_rubles_full_discount_floors_at_one_ruble() -> None:
    assert calculate_discounted_rubles(249, 100) == MIN_PAYMENT_RUBLES
    assert calculate_discounted_rubles(249, 150) == MIN_PAYMENT_RUBLES
    # Tiny price still floors at 1 ₽ (a discount never makes a payment free).
    assert calculate_discounted_rubles(1, 50) == MIN_PAYMENT_RUBLES


# ── validation ──────────────────────────────────────────────────────────────────


def test_validate_promo_states() -> None:
    assert validate_promo(None, now=_now()) is PromoValidationError.NOT_FOUND
    assert validate_promo(_promo(active=False), now=_now()) is PromoValidationError.INACTIVE
    assert validate_promo(_promo(valid_until_offset_hours=-1), now=_now()) is PromoValidationError.EXPIRED
    assert validate_promo(_promo(uses_count=1, max_uses=1), now=_now()) is PromoValidationError.EXHAUSTED


def test_validate_promo_usable_returns_none() -> None:
    assert validate_promo(_promo(), now=_now()) is None
    # No expiry → still usable.
    assert validate_promo(_promo(valid_until_offset_hours=None), now=_now()) is None


# ── in-memory repository ────────────────────────────────────────────────────────


def test_inmemory_consume_atomic_and_exhausts() -> None:
    async def main() -> None:
        repo = InMemoryPromoCodeRepository()
        await repo.create(_promo(code="CODE-ONE", max_uses=1, uses_count=0))

        assert await repo.consume("CODE-ONE") is True
        # Second consume on a 1-use code → exhausted.
        assert await repo.consume("CODE-ONE") is False
        stored = await repo.get_by_code("CODE-ONE")
        assert stored is not None and stored.uses_count == 1

        # Unknown code → False.
        assert await repo.consume("NOPE") is False

        # Inactive code → False.
        await repo.create(_promo(code="CODE-INACTIVE", active=False, max_uses=5))
        assert await repo.consume("CODE-INACTIVE") is False

        # Expired code → False.
        await repo.create(_promo(code="CODE-EXPIRED", valid_until_offset_hours=-1, max_uses=5))
        assert await repo.consume("CODE-EXPIRED") is False

    _run(main())


def test_inmemory_ensure_personal_code_is_idempotent() -> None:
    async def main() -> None:
        repo = InMemoryPromoCodeRepository()
        first = await repo.ensure_personal_code(
            owner_internal_user_id="u1",
            code="BRAVADA-XYZ",
            discount_percent=15,
            valid_days=7,
        )
        assert first.code == "BRAVADA-XYZ" and first.discount_percent == 15
        second = await repo.ensure_personal_code(
            owner_internal_user_id="u1",
            code="BRAVADA-XYZ",
            discount_percent=15,
            valid_days=7,
        )
        # Idempotent — returns the same record (not a duplicate).
        assert second.code == first.code
        assert await repo.consume("BRAVADA-XYZ") is True

    _run(main())
