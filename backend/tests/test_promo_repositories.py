"""Тесты репозиториев промокодов на in-memory реализации (без Postgres)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.promo import PromoAudience, PromoCode, PromoDiscountKind
from app.persistence.in_memory import (
    InMemoryPromoAppliedRepository,
    InMemoryPromoCodeRepository,
    InMemoryPromoRedemptionRepository,
)
from app.persistence.promo_contracts import PromoRedemptionRecord


def _promo(code: str = "WELCOME", **kw) -> PromoCode:
    now = datetime.now(UTC)
    defaults = {
        "audience": PromoAudience.NEW_USERS,
        "discount_kind": PromoDiscountKind.PERCENT,
        "discount_value": 15,
        "max_redemptions": None,
        "redemptions_count": 0,
        "expires_at": None,
        "is_active": True,
        "note": None,
        "created_at": now,
        "updated_at": now,
        "created_by": "admin",
    }
    defaults.update(kw)
    return PromoCode(code=code, **defaults)


# --- PromoCodeRepository ---


@pytest.mark.asyncio
async def test_code_crud_roundtrip():
    repo = InMemoryPromoCodeRepository()
    assert await repo.get("WELCOME") is None
    created = await repo.create(_promo())
    assert created.code == "WELCOME"
    assert (await repo.get("WELCOME")).discount_value == 15

    updated = await repo.update("WELCOME", {"discount_value": 25, "is_active": False})
    assert updated.discount_value == 25
    assert updated.is_active is False

    assert await repo.delete("WELCOME") is True
    assert await repo.delete("WELCOME") is False
    assert await repo.get("WELCOME") is None


@pytest.mark.asyncio
async def test_code_create_duplicate_raises():
    repo = InMemoryPromoCodeRepository()
    await repo.create(_promo("WELCOME"))
    with pytest.raises(ValueError, match="promo_code_exists"):
        await repo.create(_promo("WELCOME"))


@pytest.mark.asyncio
async def test_code_update_ignores_disallowed_columns():
    repo = InMemoryPromoCodeRepository()
    await repo.create(_promo("WELCOME", redemptions_count=0))
    # redemptions_count/created_by не в стоп-листе -> игнор, счётчик не меняется
    updated = await repo.update("WELCOME", {"redemptions_count": 999, "created_by": "hacker"})
    assert updated.redemptions_count == 0
    assert updated.created_by == "admin"


@pytest.mark.asyncio
async def test_code_per_user_limit_update_roundtrip():
    repo = InMemoryPromoCodeRepository()
    await repo.create(_promo("WELCOME", max_redemptions_per_user=1))
    assert (await repo.get("WELCOME")).max_redemptions_per_user == 1
    # переключение на «неограничено» и обратно — колонка в белом списке
    updated = await repo.update("WELCOME", {"max_redemptions_per_user": None})
    assert updated.max_redemptions_per_user is None
    updated = await repo.update("WELCOME", {"max_redemptions_per_user": 1})
    assert updated.max_redemptions_per_user == 1


@pytest.mark.asyncio
async def test_code_list_active_filter():
    repo = InMemoryPromoCodeRepository()
    await repo.create(_promo("A", is_active=True))
    await repo.create(_promo("B", is_active=False))
    assert len(await repo.list_codes(include_inactive=True)) == 2
    active_only = await repo.list_codes(include_inactive=False)
    assert [c.code for c in active_only] == ["A"]


@pytest.mark.asyncio
async def test_code_increment_respects_max():
    repo = InMemoryPromoCodeRepository()
    await repo.create(_promo("WELCOME", max_redemptions=2, redemptions_count=1))
    assert await repo.increment_redemptions("WELCOME", 2) is True
    assert (await repo.get("WELCOME")).redemptions_count == 2
    # лимит исчерпан
    assert await repo.increment_redemptions("WELCOME", 2) is False
    assert (await repo.get("WELCOME")).redemptions_count == 2


@pytest.mark.asyncio
async def test_code_increment_unlimited():
    repo = InMemoryPromoCodeRepository()
    await repo.create(_promo("WELCOME", max_redemptions=None, redemptions_count=0))
    for _ in range(5):
        assert await repo.increment_redemptions("WELCOME", None) is True
    assert (await repo.get("WELCOME")).redemptions_count == 5


@pytest.mark.asyncio
async def test_code_increment_missing_code():
    repo = InMemoryPromoCodeRepository()
    assert await repo.increment_redemptions("NOPE", None) is False


# --- PromoRedemptionRepository ---


def _redemption(code: str = "WELCOME", uid: str = "u1", idem: str = "k1") -> PromoRedemptionRecord:
    return PromoRedemptionRecord(
        redemption_id="r1",
        code=code,
        internal_user_id=uid,
        idempotency_key=idem,
        base_rubles=249,
        discounted_rubles=212,
        discount_kopecks=3700,
        payment_ref="pay_1",
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_redeem_idempotent_by_key_only():
    repo = InMemoryPromoRedemptionRepository()
    assert await repo.redeem(_redemption()) is True
    # дубль по idempotency_key
    assert await repo.redeem(_redemption()) is False
    # другой idem, тот же code+user -> ВСТАВЛЯЕТСЯ: per-user лимит больше не дедуп
    # репозитория, инфорсится count-гейтом в _redeem_atomic (зеркало Postgres-семантики)
    assert await repo.redeem(_redemption(idem="k2")) is True
    # другой пользователь -> ок
    assert await repo.redeem(_redemption(uid="u2", idem="k3")) is True


@pytest.mark.asyncio
async def test_redeem_count_for_user():
    repo = InMemoryPromoRedemptionRepository()
    assert await repo.count_for_user("WELCOME", "u1") == 0
    await repo.redeem(_redemption(code="WELCOME", uid="u1", idem="k1"))
    await repo.redeem(_redemption(code="WELCOME", uid="u1", idem="k2"))
    await repo.redeem(_redemption(code="OTHER", uid="u1", idem="k3"))
    await repo.redeem(_redemption(code="WELCOME", uid="u2", idem="k4"))
    assert await repo.count_for_user("WELCOME", "u1") == 2
    assert await repo.count_for_user("WELCOME", "u2") == 1
    assert await repo.count_for_user("OTHER", "u1") == 1
    assert await repo.count_for_user("GHOST", "u1") == 0


@pytest.mark.asyncio
async def test_redeem_list_by_user():
    repo = InMemoryPromoRedemptionRepository()
    await repo.redeem(_redemption(uid="u1", code="A", idem="1"))
    await repo.redeem(_redemption(uid="u1", code="B", idem="2"))
    await repo.redeem(_redemption(uid="u2", code="A", idem="3"))
    rows = await repo.list_by_user("u1")
    assert {r.code for r in rows} == {"A", "B"}


# --- PromoAppliedRepository ---


@pytest.mark.asyncio
async def test_applied_upsert_get_clear():
    repo = InMemoryPromoAppliedRepository()
    assert await repo.get("u1") is None
    exp = datetime.now(UTC) + timedelta(hours=24)
    await repo.upsert("u1", "WELCOME", exp)
    got = await repo.get("u1")
    assert got.code == "WELCOME"
    assert got.is_expired(datetime.now(UTC)) is False
    assert got.is_expired(datetime.now(UTC) + timedelta(hours=25)) is True
    # повторный upsert перезаписывает код
    await repo.upsert("u1", "SUMMER", exp)
    assert (await repo.get("u1")).code == "SUMMER"
    await repo.clear("u1")
    assert await repo.get("u1") is None
