"""Юнит-тесты shared apply-ядра промокода (in-memory репозитории, без Postgres).

Ядро — application/promo_apply_service.py::apply_promo_intent; бот и web_api —
делегаты. Здесь проверяем все статусы ветвления и идемпотентность; postgres-специфика
(compute_is_new_user по реальным таблицам) покрыта интеграционными тестами.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.application.promo_apply_service import apply_promo_intent
from app.domain.promo import PromoAudience, PromoCode, PromoDiscountKind
from app.persistence.in_memory import (
    InMemoryPromoAppliedRepository,
    InMemoryPromoCodeRepository,
    InMemoryPromoRedemptionRepository,
)
from app.persistence.promo_contracts import PromoRedemptionRecord

_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _promo(code: str = "WELCOME", **kw) -> PromoCode:
    defaults = {
        "audience": PromoAudience.NEW_USERS,
        "discount_kind": PromoDiscountKind.PERCENT,
        "discount_value": 15,
        "max_redemptions": None,
        "redemptions_count": 0,
        "expires_at": None,
        "is_active": True,
        "note": None,
        "created_at": _NOW,
        "updated_at": _NOW,
        "created_by": "admin",
    }
    defaults.update(kw)
    return PromoCode(code=code, **defaults)


class _Repos:
    def __init__(self) -> None:
        self.codes = InMemoryPromoCodeRepository()
        self.redemptions = InMemoryPromoRedemptionRepository()
        self.applied = InMemoryPromoAppliedRepository()

    async def apply(self, raw_code: str, internal_user_id: str = "web_abc"):
        return await apply_promo_intent(
            pool=None,
            promo_code_repo=self.codes,
            promo_redemption_repo=self.redemptions,
            promo_applied_repo=self.applied,
            snapshots=None,
            internal_user_id=internal_user_id,
            raw_code=raw_code,
            now=_NOW,
        )


@pytest.mark.asyncio
async def test_invalid_format() -> None:
    outcome = await _Repos().apply("кофе с крокодилом")
    assert outcome.status == "invalid"
    assert outcome.promo is None


@pytest.mark.asyncio
async def test_not_found() -> None:
    outcome = await _Repos().apply("SOMECODE")
    assert outcome.status == "not_found"
    assert outcome.code == "SOMECODE"


@pytest.mark.asyncio
async def test_inactive_carries_block_reason() -> None:
    repos = _Repos()
    await repos.codes.create(_promo(is_active=False))
    outcome = await repos.apply("WELCOME")
    assert outcome.status == "inactive"
    assert outcome.block_reason == "promo_inactive"

    repos = _Repos()
    await repos.codes.create(_promo(expires_at=_NOW - timedelta(days=1)))
    outcome = await repos.apply("WELCOME")
    assert outcome.status == "inactive"
    assert outcome.block_reason == "promo_expired"

    repos = _Repos()
    await repos.codes.create(_promo(max_redemptions=10, redemptions_count=10))
    outcome = await repos.apply("WELCOME")
    assert outcome.status == "inactive"
    assert outcome.block_reason == "promo_exhausted"


@pytest.mark.asyncio
async def test_already_used_per_user_limit() -> None:
    repos = _Repos()
    await repos.codes.create(_promo(max_redemptions_per_user=1))
    await repos.redemptions.redeem(
        PromoRedemptionRecord(
            redemption_id="r1",
            code="WELCOME",
            internal_user_id="web_abc",
            idempotency_key="k1",
            base_rubles=249,
            discounted_rubles=211,
            discount_kopecks=3800,
            payment_ref=None,
            created_at=_NOW,
        )
    )
    outcome = await repos.apply("WELCOME")
    assert outcome.status == "already_used"
    assert outcome.promo is not None


@pytest.mark.asyncio
async def test_ok_stores_intent_with_ttl() -> None:
    repos = _Repos()
    await repos.codes.create(_promo())
    outcome = await repos.apply("  welcome  ")  # normalize: trim + upper
    assert outcome.status == "ok"
    assert outcome.code == "WELCOME"
    applied = await repos.applied.get("web_abc")
    assert applied is not None and applied.code == "WELCOME"
    assert applied.expires_at - _NOW == timedelta(hours=24)


@pytest.mark.asyncio
async def test_reapply_same_code_is_idempotent() -> None:
    """Повторный apply того же кода — ок и не ломает promo_applied (PK=internal_user_id)."""
    repos = _Repos()
    await repos.codes.create(_promo())
    assert (await repos.apply("WELCOME")).status == "ok"
    assert (await repos.apply("WELCOME")).status == "ok"
    applied = await repos.applied.get("web_abc")
    assert applied is not None
