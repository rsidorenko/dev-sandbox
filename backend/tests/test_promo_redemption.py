"""Фаза 3: разрешение скидки, атомарное погашение, сверка вебхука, паритет комиссии.

In-memory seam (pool=None) — как test_balance_payment_service. Покрывает инварианты,
проверенные адверсариально: итог в целых рублях, clamp, идемпотентность погашения
(двойной клик/повтор вебхука), комиссия с DISCOUNTED суммы (паритет карта/баланс),
сверка вебхука с/без discount_kopecks (legacy=0). Глубокая postgres-проверка погашения
(advisory lock + UNIQUE) — в postgres-smoke (opt-in DATABASE_URL).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.balance_payment_service import (
    BalancePaymentStatus,
    add_device_from_balance,
    pay_plan_from_balance,
)
from app.application.bootstrap import build_slice1_composition
from app.application.interfaces import SubscriptionSnapshot
from app.application.promo_service import (
    redeem_promo_on_success,
    resolve_active_promo,
)
from app.domain.devices import extra_device_cost
from app.domain.plans import get_plan
from app.domain.promo import (
    PROMO_APPLIED_TTL_HOURS,
    PromoAudience,
    PromoCode,
    PromoDiscountKind,
    apply_promo_discount,
)
from app.domain.referral import build_commissions_for_payment
from app.issuance.vless_provider import StubVlessProvider
from app.shared.test_helpers import run_async as _run
from app.yookassa.webhook import _expected_subscription_kopecks, _promo_from_metadata

_FUTURE = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)


def _promo(
    *,
    code: str = "WELCOME",
    audience: PromoAudience = PromoAudience.NEW_USERS,
    kind: PromoDiscountKind = PromoDiscountKind.PERCENT,
    value: int = 50,
    discounts_by_plan: dict[str, int] | None = None,
    max_redemptions: int | None = None,
    redemptions_count: int = 0,
    is_active: bool = True,
    expires_at: datetime | None = None,
) -> PromoCode:
    now = datetime.now(UTC)
    return PromoCode(
        code=code,
        audience=audience,
        discount_kind=kind,
        discount_value=value,
        max_redemptions=max_redemptions,
        redemptions_count=redemptions_count,
        expires_at=expires_at,
        is_active=is_active,
        note=None,
        created_at=now,
        updated_at=now,
        created_by="admin",
        discounts_by_plan=discounts_by_plan,
    )


def _composition(user_id: int = 9001, balance_kopecks: int = 0):
    c = build_slice1_composition(bot_username="testbot", vless_provider=StubVlessProvider())
    _run(c.identity.create_if_absent(user_id))
    id_rec = _run(c.identity.find_by_telegram_user_id(user_id))
    if balance_kopecks > 0:
        _run(c.referral_balance_repo.credit(id_rec.internal_user_id, balance_kopecks))
    return c, id_rec.internal_user_id


def _apply(c, uid: str, promo: PromoCode) -> None:
    """Создать код и применить его пользователю (как bot _handle_promo_code_input)."""
    _run(c.promo_code_repo.create(promo))
    expires = datetime.now(UTC) + timedelta(hours=PROMO_APPLIED_TTL_HOURS)
    _run(c.promo_applied_repo.upsert(uid, promo.code, expires))


def _resolve(c, uid: str, base_rubles: int):
    return _run(
        resolve_active_promo(
            pool=None,
            promo_applied_repo=c.promo_applied_repo,
            promo_code_repo=c.promo_code_repo,
            snapshots=c.snapshots,
            internal_user_id=uid,
            base_rubles=base_rubles,
        )
    )


def _redeem(c, uid: str, *, code: str, payment_ref: str, base=249, discounted=125, dk=12400) -> bool:
    return _run(
        redeem_promo_on_success(
            pool=None,
            promo_applied_repo=c.promo_applied_repo,
            promo_code_repo=c.promo_code_repo,
            promo_redemption_repo=c.promo_redemption_repo,
            internal_user_id=uid,
            code=code,
            base_rubles=base,
            discounted_rubles=discounted,
            discount_kopecks=dk,
            payment_ref=payment_ref,
        )
    )


# ── resolve_active_promo: каноническая re-валидация ─────────────────────────


def test_resolve_discount_for_eligible_new_user():
    # 50% от 249 = 124.5 → half-up = 125. discount_kopecks = (249-125)*100 = 12400.
    c, uid = _composition()
    _apply(c, uid, _promo(value=50))
    active = _resolve(c, uid, 249)
    assert active is not None
    assert active.code == "WELCOME"
    assert active.price.base_rubles == 249
    assert active.price.discounted_rubles == 125
    assert active.price.discount_kopecks == 12400


def test_resolve_audience_mismatch_returns_none():
    # existing_users промокод для нового пользователя → None (fail-closed, полная цена).
    c, uid = _composition()
    _apply(c, uid, _promo(audience=PromoAudience.EXISTING_USERS, value=10))
    assert _resolve(c, uid, 249) is None


def test_resolve_existing_user_eligible_for_existing_promo():
    # пользователь С активной подпиской → is_new=False → existing_users промокод применим.
    c, uid = _composition()
    _run(
        c.snapshots.upsert_state(
            SubscriptionSnapshot(
                internal_user_id=uid, state_label="active", active_until_utc=_FUTURE, plan_id="1m", device_count=5
            )
        )
    )
    _apply(c, uid, _promo(audience=PromoAudience.EXISTING_USERS, value=10))
    active = _resolve(c, uid, 249)
    assert active is not None
    assert active.price.discounted_rubles == 224  # 10% от 249 = 224.1 → 224


def test_resolve_expired_applied_returns_none():
    c, uid = _composition()
    _run(c.promo_code_repo.create(_promo()))
    expired = datetime.now(UTC) - timedelta(hours=1)
    _run(c.promo_applied_repo.upsert(uid, "WELCOME", expired))
    assert _resolve(c, uid, 249) is None


def test_resolve_inactive_code_returns_none():
    c, uid = _composition()
    _apply(c, uid, _promo(is_active=False))
    assert _resolve(c, uid, 249) is None


def test_resolve_exhausted_code_returns_none():
    c, uid = _composition()
    _apply(c, uid, _promo(max_redemptions=10, redemptions_count=10))
    assert _resolve(c, uid, 249) is None


def test_resolve_zero_base_returns_none():
    c, uid = _composition()
    _apply(c, uid, _promo())
    assert _resolve(c, uid, 0) is None  # add-device без доплаты — скидывать нечего


# ── redeem_promo_on_success: идемпотентность и guard лимита ──────────────────


def test_redeem_idempotent_double_call_no_double_increment():
    c, uid = _composition()
    _apply(c, uid, _promo(max_redemptions=100))
    first = _redeem(c, uid, code="WELCOME", payment_ref="pay-1")
    second = _redeem(c, uid, code="WELCOME", payment_ref="pay-1")  # повтор вебхука/двойной клик
    assert first is True
    assert second is False  # UNIQUE(idempotency_key) гасит дубль
    promo = _run(c.promo_code_repo.get("WELCOME"))
    assert promo is not None and promo.redemptions_count == 1  # инкремент один раз
    assert _run(c.promo_applied_repo.get(uid)) is None  # намерение очищено
    assert len(_run(c.promo_redemption_repo.list_by_user(uid))) == 1


def test_redeem_exhausted_guard_records_but_does_not_overincrement():
    # Лимит уже исчерпан (redemptions_count == max). Погашение всё равно записываем (аудит
    # факта — деньги уже списаны), но счётчик не превышает max.
    c, uid = _composition()
    _apply(c, uid, _promo(max_redemptions=1, redemptions_count=1))
    inserted = _redeem(c, uid, code="WELCOME", payment_ref="pay-x")
    assert inserted is True  # запись погашения вставлена
    promo = _run(c.promo_code_repo.get("WELCOME"))
    assert promo is not None and promo.redemptions_count == 1  # не выросло сверх лимита
    assert len(_run(c.promo_redemption_repo.list_by_user(uid))) == 1


def test_redeem_empty_code_is_noop():
    c, uid = _composition()
    assert _redeem(c, uid, code="", payment_ref="pay") is False


# ── webhook: сверка суммы с/без discount_kopecks (legacy=0) ──────────────────


def test_expected_kopecks_legacy_no_discount_is_base():
    plan = get_plan("1m")
    assert _expected_subscription_kopecks(plan, 5) == 249_00
    assert _expected_subscription_kopecks(plan, 5, discount_kopecks=0) == 249_00


def test_expected_kopecks_with_discount_subtracts():
    plan = get_plan("1m")
    # base 24900 - discount 12400 = 12500 = то, что списано (round-trip create_payment↔webhook).
    assert _expected_subscription_kopecks(plan, 5, discount_kopecks=12400) == 125_00


def test_expected_kopecks_clamps_negative_discount():
    plan = get_plan("1m")
    # max(0, dk) — отрицательная скидка не может увеличить ожидаемую сумму (anti-tamper).
    assert _expected_subscription_kopecks(plan, 5, discount_kopecks=-999) == 249_00


def test_promo_from_metadata_legacy_returns_none():
    assert _promo_from_metadata({"plan_id": "1m"}) is None
    assert _promo_from_metadata({"promo_code": "", "discount_kopecks": "100"}) is None
    assert _promo_from_metadata({}) is None


def test_promo_from_metadata_parses_code_and_discount():
    assert _promo_from_metadata({"promo_code": "WELCOME", "discount_kopecks": "12400"}) == ("WELCOME", 12400)
    # legacy-платёж без discount_kopecks → 0 (безопасный parse).
    assert _promo_from_metadata({"promo_code": "WELCOME"}) == ("WELCOME", 0)
    assert _promo_from_metadata({"promo_code": "WELCOME", "discount_kopecks": "oops"}) == ("WELCOME", 0)


# ── баланс-подписка: скидка в debit/amount + комиссия с DISCOUNTED базы ─────


def test_balance_subscription_discount_and_commission_parity():
    c, uid = _composition(balance_kopecks=500_00)
    referrer = "u_ref"
    _run(
        c.referral_relationship_repo.create_relationship(
            referred_user_id=uid, referrer_user_id=referrer, level=1, referrer_of_referrer_user_id=None
        )
    )
    _apply(c, uid, _promo(value=50))  # NEW_USERS 50%: 249 → 125

    result = _run(
        pay_plan_from_balance(
            pool=None,
            composition=c,
            internal_user_id=uid,
            plan_id="1m",
            device_count=5,
            vless_provider=StubVlessProvider(),
        )
    )
    assert result.status is BalancePaymentStatus.COMPLETED
    assert result.amount_kopecks == 125_00  # discounted дебит
    bal = _run(c.referral_balance_repo.get_balance(uid))
    assert bal is not None and bal.balance_kopecks == 500_00 - 125_00

    # Комиссия реферера считается с DISCOUNTED суммы (паритет карта/баланс), НЕ с полной 24900.
    discounted_comm = build_commissions_for_payment(
        payer_user_id=uid,
        direct_referrer_user_id=referrer,
        indirect_referrer_user_id=None,
        plan_id="1m",
        payment_amount_kopecks=125_00,
    )
    full_comm = build_commissions_for_payment(
        payer_user_id=uid,
        direct_referrer_user_id=referrer,
        indirect_referrer_user_id=None,
        plan_id="1m",
        payment_amount_kopecks=249_00,
    )
    ref_bal = _run(c.referral_balance_repo.get_balance(referrer))
    assert ref_bal is not None
    assert ref_bal.balance_kopecks == discounted_comm[0].amount_kopecks
    assert ref_bal.balance_kopecks != full_comm[0].amount_kopecks  # скидка учтена в комиссии

    # Погашение записано, намерение очищено.
    assert len(_run(c.promo_redemption_repo.list_by_user(uid))) == 1
    assert _run(c.promo_applied_repo.get(uid)) is None


# ── баланс add-device: скидка к стоимости доплаты за устройства ──────────────


def test_balance_add_device_discount_applied():
    c, uid = _composition(balance_kopecks=500_00)
    _run(
        c.snapshots.upsert_state(
            SubscriptionSnapshot(
                internal_user_id=uid, state_label="active", active_until_utc=_FUTURE, plan_id="1m", device_count=5
            )
        )
    )
    promo = _promo(audience=PromoAudience.EXISTING_USERS, value=50)
    _apply(c, uid, promo)

    base = extra_device_cost(7, 5, 30)
    assert base > 0
    result = _run(add_device_from_balance(pool=None, composition=c, internal_user_id=uid, new_device_count=7))
    assert result.status is BalancePaymentStatus.COMPLETED
    expected = apply_promo_discount(base, promo)
    assert result.amount_kopecks == expected.discounted_kopecks
    # погашение записано
    assert len(_run(c.promo_redemption_repo.list_by_user(uid))) == 1


# ── bot _handle_promo_code_input: валидация, применение, rate-limit ──────────


def test_bot_promo_input_applies_valid_code():
    from app.bot_transport.runtime_facade import _handle_promo_code_input

    c, uid = _composition()
    _run(c.promo_code_repo.create(_promo(value=15)))
    text, keyboard = _run(_handle_promo_code_input(c, 9001, "  welcome  "))  # trim+upper
    assert "применён" in text
    applied = _run(c.promo_applied_repo.get(uid))
    assert applied is not None and applied.code == "WELCOME"
    assert keyboard is not None  # main menu keyboard


def test_bot_promo_input_rejects_bad_format():
    from app.bot_transport.runtime_facade import _handle_promo_code_input

    c, _uid = _composition()
    text, _kb = _run(_handle_promo_code_input(c, 9001, "AB"))  # слишком короткий
    assert "некорректный" in text


def test_bot_promo_input_not_found():
    from app.bot_transport.runtime_facade import _handle_promo_code_input

    c, _uid = _composition()
    text, _kb = _run(_handle_promo_code_input(c, 9001, "GHOST"))
    assert "не найден" in text


def test_bot_promo_input_rate_limited_after_window():
    # PROMOCODE rule = 5/min. Шесть быстрых попыток → шестая упирается в лимитер.
    from app.bot_transport.runtime_facade import _handle_promo_code_input

    c, _uid = _composition()
    for _ in range(5):
        text, _kb = _run(_handle_promo_code_input(c, 9001, "GHOST"))
        assert "Слишком много" not in text
    text, _kb = _run(_handle_promo_code_input(c, 9001, "GHOST"))
    assert "Слишком много" in text


# ── per-tariff скидки: resolve_active_promo(plan_id, is_addon) ───────────────


def _resolve_plan(c, uid: str, base_rubles: int, *, plan_id: str | None, is_addon: bool = False):
    return _run(
        resolve_active_promo(
            pool=None,
            promo_applied_repo=c.promo_applied_repo,
            promo_code_repo=c.promo_code_repo,
            snapshots=c.snapshots,
            internal_user_id=uid,
            base_rubles=base_rubles,
            plan_id=plan_id,
            is_addon=is_addon,
        )
    )


def test_resolve_per_plan_discount_for_configured_plan():
    # 1m: 10% от 249 → 224.
    c, uid = _composition()
    _apply(c, uid, _promo(kind=PromoDiscountKind.PERCENT, value=0, discounts_by_plan={"1m": 10, "3m": 15}))
    active = _resolve_plan(c, uid, 249, plan_id="1m")
    assert active is not None
    assert active.price.discounted_rubles == 224
    assert active.price.discount_kopecks == 2500


def test_resolve_per_plan_no_discount_for_custom_tariff():
    # «Свой» — промокод валиден, но скидки на этот тариф нет.
    c, uid = _composition()
    _apply(c, uid, _promo(discounts_by_plan={"1m": 50}))
    active = _resolve_plan(c, uid, 300, plan_id="custom:20")
    assert active is not None
    assert active.price.discounted_rubles == 300
    assert active.price.discount_kopecks == 0


def test_resolve_per_plan_no_discount_for_unconfigured_plan():
    c, uid = _composition()
    _apply(c, uid, _promo(discounts_by_plan={"1m": 50}))
    active = _resolve_plan(c, uid, 699, plan_id="3m")
    assert active is not None
    assert active.price.discounted_rubles == 699
    assert active.price.discount_kopecks == 0


def test_resolve_per_plan_no_discount_for_addon():
    # add-device → is_addon=True → без скидки даже для тарифа из map.
    c, uid = _composition()
    _apply(c, uid, _promo(discounts_by_plan={"1m": 50}))
    active = _resolve_plan(c, uid, 80, plan_id="1m", is_addon=True)
    assert active is not None
    assert active.price.discounted_rubles == 80
    assert active.price.discount_kopecks == 0


def test_balance_subscription_threads_plan_id_for_per_plan_discount():
    # pay_plan_from_balance → _resolve_balance_discount(plan_id=…) → per-tariff скидка.
    c, uid = _composition(balance_kopecks=500_00)
    _apply(c, uid, _promo(kind=PromoDiscountKind.PERCENT, value=0, discounts_by_plan={"1m": 10}))
    result = _run(
        pay_plan_from_balance(
            pool=None,
            composition=c,
            internal_user_id=uid,
            plan_id="1m",
            device_count=5,
            vless_provider=StubVlessProvider(),
        )
    )
    assert result.status is BalancePaymentStatus.COMPLETED
    assert result.amount_kopecks == 224_00  # 249 −10% = 224 рубля
