"""Юнит-тесты обработчика кнопки промокода рассылки ``_handle_broadcast_promo_claim``.

Покрываем: отказы gate (uid/identity/pool/rate-limit/non-recipient/expired TTL — единый
текст ``_broadcast_bonus_unavailable_text``), pre-check «уже применён» (дружелюбный текст
без повторного apply), успех apply (→ ``text_promo_applied``), ветки not_found/inactive/
ineligible, rate-limit fail-open и routing ``bc_promo:`` через весь pipeline.

В отличие от кнопки дней, apply промокода идемпотентен (``promo_applied`` PK=internal_user_id),
поэтому gate — обычный SELECT (а не atomic UPDATE…RETURNING): None = любая причина отказа
(не получатель / TTL истёк / уже было). Composition — реальная in-memory (как в
test_broadcast_bonus_claim), пул — локальный фейк, apply-ядро — monkeypatch.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.application.bootstrap import build_slice1_composition
from app.application.broadcast_funnel import payment_callback
from app.application.interfaces import SubscriptionSnapshot
from app.application.plan_pricing import PlanPricingProvider
from app.application.telegram_command_rate_limit import InMemoryTelegramCommandRateLimiter
from app.bot_transport.runtime_facade import (
    _broadcast_bonus_unavailable_text,
    _handle_broadcast_promo_claim,
    handle_slice1_telegram_update_to_rendered_message,
)
from app.domain.promo import PromoAudience, PromoCode, PromoDiscountKind
from app.persistence.in_memory import InMemoryPromoAppliedRepository
from app.persistence.promo_contracts import PromoAppliedRecord
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run

_JOB_ID = "11111111-2222-3333-4444-555555555555"
_CODE = "SUMMER25"


@pytest.fixture(autouse=True)
def pricing_without_postgres(monkeypatch):
    provider = PlanPricingProvider(None)
    monkeypatch.setattr("app.bot_transport.runtime_facade.get_plan_pricing", lambda pool: provider)


def _buttons(keyboard):
    return [button for row in keyboard["inline_keyboard"] for button in row]


def _start_update(*, uid: int = 42, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": uid, "is_bot": False, "first_name": "U"},
            "chat": {"id": uid, "type": "private"},
            "text": "/start",
        },
    }


async def _register(c, *, uid: int = 42) -> str:
    """Регистрирует юзера и возвращает его internal_user_id (для засева applied-записи)."""
    await handle_slice1_telegram_update_to_rendered_message(
        _start_update(uid=uid), c, correlation_id=new_correlation_id()
    )
    rec = await c.identity.find_by_telegram_user_id(uid)
    assert rec is not None
    return rec.internal_user_id


def _make_promo(*, code: str = _CODE, audience: PromoAudience = PromoAudience.ALL_USERS) -> PromoCode:
    return PromoCode(
        code=code,
        audience=audience,
        discount_kind=PromoDiscountKind.PERCENT,
        discount_value=15,
        max_redemptions=None,
        redemptions_count=0,
        expires_at=None,
        is_active=True,
        note=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_by=None,
        discounts_by_plan=None,
    )


class _PromoClaimPool:
    """Фейк-пул для обработчика: единственный запрос — gate SELECT broadcast_promo_claims.

    Возвращает ``claim_row`` (``{"code": ...}``) или None (все причины отказа).
    """

    def __init__(self, *, claim_row: dict | None = None) -> None:
        self.claim_row = claim_row
        self.claim_attempts = 0
        self.bonus_events = []

    async def execute(self, sql, *args):
        if "broadcast_bonus_funnel" in sql:
            self.bonus_events.append(args)
        return "INSERT 0 1"

    async def fetchrow(self, sql: str, *args):
        if "broadcast_promo_claims" in sql:
            self.claim_attempts += 1
            return self.claim_row
        return None


class _FakeGate:
    def __init__(self, result: bool | None) -> None:
        self.result = result

    async def is_member(self, user_id: int) -> bool | None:
        return self.result


def test_promo_claim_refuses_when_uid_none() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        text, _kb = await _handle_broadcast_promo_claim(c, None, _JOB_ID)
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_promo_claim_refuses_when_job_id_empty() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        text, _kb = await _handle_broadcast_promo_claim(c, 42, "")
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_promo_claim_refuses_when_rate_limited() -> None:
    """Soft anti-spam: лимитер блокит спам-клики до обращения к БД (как у кнопки дней)."""

    async def main() -> None:
        c = dataclasses.replace(
            build_slice1_composition(),
            command_rate_limiter=InMemoryTelegramCommandRateLimiter(broadcast_claim_limit=0, now_seconds=lambda: 100.0),
        )
        await _register(c, uid=42)
        text, _kb = await _handle_broadcast_promo_claim(c, 42, _JOB_ID)
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_promo_claim_refuses_when_identity_not_found() -> None:
    """Юзер не зарегистрирован → нет identity → отказ тем же текстом."""

    async def main() -> None:
        c = build_slice1_composition()
        # uid=77 не регистрируем
        text, _kb = await _handle_broadcast_promo_claim(c, 77, _JOB_ID)
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_promo_claim_refuses_when_pool_missing() -> None:
    """In-memory composition без postgres-пула → отказ (gate требует БД)."""

    async def main() -> None:
        c = build_slice1_composition()
        await _register(c, uid=42)
        text, _kb = await _handle_broadcast_promo_claim(c, 42, _JOB_ID)
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_promo_claim_refuses_when_no_claim_row() -> None:
    """Gate None — единый отказ для всех причин: не получатель / TTL истёк / переслан чужану."""

    async def main() -> None:
        c = build_slice1_composition()
        await _register(c, uid=42)
        c.identity._pool = _PromoClaimPool(claim_row=None)  # type: ignore[attr-defined]
        text, _kb = await _handle_broadcast_promo_claim(c, 42, _JOB_ID)
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_promo_claim_requires_channel_subscription_before_looking_up_code() -> None:
    """Неподписанный не получает доступ к промокоду и не меняет promo_applied."""

    async def main() -> None:
        c = build_slice1_composition(trial_channel_gate=_FakeGate(False))
        await _register(c, uid=42)
        pool = _PromoClaimPool(claim_row={"code": _CODE})
        c.identity._pool = pool  # type: ignore[attr-defined]
        text, keyboard = await _handle_broadcast_promo_claim(c, 42, _JOB_ID)
        return text, keyboard, pool

    text, keyboard, pool = _run(main())
    assert "Подарки и промокоды — для подписчиков канала" in text
    assert pool.claim_attempts == 0
    assert keyboard is not None
    assert any(
        button.get("callback_data") == f"bc_promo:{_JOB_ID}" for row in keyboard["inline_keyboard"] for button in row
    )


def test_promo_claim_precheck_already_applied(monkeypatch) -> None:
    """Pre-check: активная applied-запись с тем же code → дружелюбный текст БЕЗ повторного apply."""

    apply_calls: list = []

    async def _fake_apply(*args, **kwargs):
        apply_calls.append((args, kwargs))
        return "ok", _CODE, _make_promo()

    monkeypatch.setattr("app.bot_transport.runtime_facade._apply_promo_to_user", _fake_apply)

    async def main() -> None:
        c = build_slice1_composition()
        iid = await _register(c, uid=42)
        await c.promo_code_repo.create(_make_promo())
        # Засеваем active applied-запись с тем же code, что и в claim-реестре.
        assert isinstance(c.promo_applied_repo, InMemoryPromoAppliedRepository)
        c.promo_applied_repo._applied[iid] = PromoAppliedRecord(  # type: ignore[attr-defined]
            internal_user_id=iid,
            code=_CODE,
            applied_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )
        c.identity._pool = _PromoClaimPool(claim_row={"code": _CODE})  # type: ignore[attr-defined]
        return await _handle_broadcast_promo_claim(c, 42, _JOB_ID)

    text, _kb = _run(main())
    assert _CODE in text
    assert "применён" in text
    # pre-check сработал → apply-ядро НЕ вызывалось
    assert apply_calls == []
    assert any(b.get("callback_data") == payment_callback(_JOB_ID, "1m", 5) for b in _buttons(_kb))


def test_promo_claim_apply_success(monkeypatch) -> None:
    """Успех: apply-ядро вернуло ('ok', code, promo) → текст «Промокод применён» со скидкой."""

    promo = _make_promo()

    async def _fake_apply(composition, internal_user_id, raw_code, *, now=None):
        assert internal_user_id  # resolved iid, не f"u{uid}"
        return "ok", _CODE, promo

    monkeypatch.setattr("app.bot_transport.runtime_facade._apply_promo_to_user", _fake_apply)

    async def main() -> None:
        c = build_slice1_composition()
        await _register(c, uid=42)
        c.identity._pool = _PromoClaimPool(claim_row={"code": _CODE})  # type: ignore[attr-defined]
        return await _handle_broadcast_promo_claim(c, 42, _JOB_ID)

    text, _kb = _run(main())
    assert "✅ Промокод SUMMER25 применён!" in text
    assert "Скидка: 15% от стоимости." in text
    assert "Устройств: 5" in text
    button = next(b for b in _buttons(_kb) if b.get("callback_data") == payment_callback(_JOB_ID, "1m", 5))
    assert "212 ₽ вместо 249" in button["text"]
    assert any(b.get("callback_data") == "buy_vpn" for b in _buttons(_kb))


def test_promo_claim_not_found_treated_as_not_found(monkeypatch) -> None:
    """Код исчез/истёк/неактивен/исчерпан между send и кликом → «не найден» (fail-closed)."""

    async def _fake_apply(composition, internal_user_id, raw_code, *, now=None):
        return "not_found", _CODE, None

    monkeypatch.setattr("app.bot_transport.runtime_facade._apply_promo_to_user", _fake_apply)

    async def main() -> None:
        c = build_slice1_composition()
        await _register(c, uid=42)
        c.identity._pool = _PromoClaimPool(claim_row={"code": _CODE})  # type: ignore[attr-defined]
        return await _handle_broadcast_promo_claim(c, 42, _JOB_ID)

    text, _kb = _run(main())
    assert "Промокод не найден или больше неактивен." in text


def test_promo_claim_inactive_treated_as_not_found(monkeypatch) -> None:
    """Статус inactive (redeem_block_reason) маппится в «не найден», как и текстовый ввод."""

    async def _fake_apply(composition, internal_user_id, raw_code, *, now=None):
        return "inactive", _CODE, _make_promo()

    monkeypatch.setattr("app.bot_transport.runtime_facade._apply_promo_to_user", _fake_apply)

    async def main() -> None:
        c = build_slice1_composition()
        await _register(c, uid=42)
        c.identity._pool = _PromoClaimPool(claim_row={"code": _CODE})  # type: ignore[attr-defined]
        return await _handle_broadcast_promo_claim(c, 42, _JOB_ID)

    text, _kb = _run(main())
    assert "Промокод не найден или больше неактивен." in text


def test_promo_claim_ineligible(monkeypatch) -> None:
    """Audience-mismatch (promo new_users, получатель платил) → «недоступен» (fail-closed)."""

    async def _fake_apply(composition, internal_user_id, raw_code, *, now=None):
        return "ineligible", _CODE, _make_promo(audience=PromoAudience.NEW_USERS)

    monkeypatch.setattr("app.bot_transport.runtime_facade._apply_promo_to_user", _fake_apply)

    async def main() -> None:
        c = build_slice1_composition()
        await _register(c, uid=42)
        c.identity._pool = _PromoClaimPool(claim_row={"code": _CODE})  # type: ignore[attr-defined]
        return await _handle_broadcast_promo_claim(c, 42, _JOB_ID)

    text, _kb = _run(main())
    assert "недоступен для вашей учётной записи" in text


def test_promo_claim_already_used_after_redemption(monkeypatch) -> None:
    """Once-режим: юзер уже погашал код → «уже активировали» (fail-closed, повторного apply нет)."""

    async def _fake_apply(composition, internal_user_id, raw_code, *, now=None):
        return "already_used", _CODE, _make_promo()

    monkeypatch.setattr("app.bot_transport.runtime_facade._apply_promo_to_user", _fake_apply)

    async def main() -> None:
        c = build_slice1_composition()
        await _register(c, uid=42)
        c.identity._pool = _PromoClaimPool(claim_row={"code": _CODE})  # type: ignore[attr-defined]
        return await _handle_broadcast_promo_claim(c, 42, _JOB_ID)

    text, _kb = _run(main())
    assert "уже активировали этот промокод ранее" in text


def test_promo_claim_rate_limiter_fail_open_on_exception(monkeypatch) -> None:
    """Сбой лимитера не должен блокировать применение промокода (fail-open)."""

    async def _fake_apply(composition, internal_user_id, raw_code, *, now=None):
        return "ok", _CODE, _make_promo()

    monkeypatch.setattr("app.bot_transport.runtime_facade._apply_promo_to_user", _fake_apply)

    class _BoomLimiter:
        async def allow(self, *, telegram_user_id, command_key):
            raise RuntimeError("limiter down")

    async def main() -> None:
        c = dataclasses.replace(build_slice1_composition(), command_rate_limiter=_BoomLimiter())
        await _register(c, uid=42)
        c.identity._pool = _PromoClaimPool(claim_row={"code": _CODE})  # type: ignore[attr-defined]
        return await _handle_broadcast_promo_claim(c, 42, _JOB_ID)

    text, _kb = _run(main())
    # fail-open: пошли дальше лимитера и дошли до apply → текст успеха, не отказ
    assert text != _broadcast_bonus_unavailable_text()
    assert "✅ Промокод SUMMER25 применён!" in text


def test_callback_bc_promo_routes_through_pipeline_to_handler() -> None:
    """Регресс routing: callback 'bc_promo:{job_id}' проходит gate+dispatcher до обработчика.
    In-memory composition без postgres-пула → единый текст отказа (как во всех путях отказа)."""

    async def main() -> None:
        c = build_slice1_composition()
        await _register(c, uid=42)
        raw = {
            "update_id": 2,
            "callback_query": {"id": "cq1", "from": {"id": 42}, "data": f"bc_promo:{_JOB_ID}"},
        }
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=new_correlation_id())
        return pkg.message_text

    assert _run(main()) == _broadcast_bonus_unavailable_text()


@pytest.mark.parametrize(
    "changes",
    [
        {"is_active": False},
        {"expires_at": datetime(2020, 1, 1, tzinfo=UTC)},
        {"max_redemptions": 1, "redemptions_count": 1},
        {"audience": PromoAudience.EXISTING_USERS},
    ],
)
def test_repeated_claim_revalidates_promo_without_extending_ttl(changes):
    async def main():
        c = build_slice1_composition()
        iid = await _register(c)
        await c.promo_code_repo.create(_make_promo())
        c.identity._pool = _PromoClaimPool(claim_row={"code": _CODE})
        await _handle_broadcast_promo_claim(c, 42, _JOB_ID)
        before = await c.promo_applied_repo.get(iid)
        await c.promo_code_repo.update(_CODE, changes)
        if changes.get("redemptions_count"):
            await c.promo_code_repo.increment_redemptions(_CODE, 1)
        text, keyboard = await _handle_broadcast_promo_claim(c, 42, _JOB_ID)
        assert "больше неактивен" in text
        assert not any(b.get("callback_data", "").startswith(("do_pay:", "bp:")) for b in _buttons(keyboard))
        assert await c.promo_applied_repo.get(iid) == before

    _run(main())


@pytest.mark.parametrize("global_wins", [False, True])
def test_claim_to_payment_pipeline_preserves_devices_price_and_promo(monkeypatch, global_wins):
    from app.domain.pricing import GlobalDiscount

    async def loader():
        return {"1m": 300}, GlobalDiscount(True, PromoDiscountKind.PERCENT, 50) if global_wins else None

    pricing = PlanPricingProvider(None, loader=loader)
    monkeypatch.setattr("app.bot_transport.runtime_facade.get_plan_pricing", lambda pool: pricing)
    payments = []

    class FakeYooKassa:
        async def create_payment(self, **kwargs):
            payments.append(kwargs)
            return SimpleNamespace(
                payment_id="test-broadcast-payment", confirmation_url="https://payments.example/confirm"
            )

    monkeypatch.setattr("app.yookassa.client.YooKassaClient.from_env", lambda: FakeYooKassa())

    async def main():
        c = build_slice1_composition()
        iid = await _register(c)
        await c.snapshots.upsert_state(SubscriptionSnapshot(iid, "active", device_count=7, plan_id="1m"))
        await c.promo_code_repo.create(dataclasses.replace(_make_promo(), discounts_by_plan={"1m": 25}))
        c.identity._pool = _PromoClaimPool(claim_row={"code": _CODE})

        async def click(data, update_id):
            return await handle_slice1_telegram_update_to_rendered_message(
                {"update_id": update_id, "callback_query": {"id": str(update_id), "from": {"id": 42}, "data": data}},
                c,
                correlation_id=new_correlation_id(),
            )

        claimed = await click(f"bc_promo:{_JOB_ID}", 2)
        assert "Устройств: 7" in claimed.message_text
        applied = await c.promo_applied_repo.get(iid)
        repeated = await click(f"bc_promo:{_JOB_ID}", 3)
        assert repeated.reply_markup == claimed.reply_markup
        assert await c.promo_applied_repo.get(iid) == applied
        assert payments == []
        button = next(
            b for b in _buttons(claimed.reply_markup) if b.get("callback_data") == payment_callback(_JOB_ID, "1m", 7)
        )
        # 300 + 2 × 80 = 460; скидка 25% либо глобальные 50%.
        expected = 230 if global_wins else 345
        assert f"{expected} ₽ вместо 460" in button["text"]
        result = await click(button["callback_data"], 4)
        assert any(b.get("url") == "https://payments.example/confirm" for b in _buttons(result.reply_markup))
        assert len(payments) == 1
        payment = payments[0]
        assert (payment["plan_id"], payment["device_count"], payment["amount_rubles"]) == ("1m", 7, expected)
        assert payment["metadata"]["base_amount_kopecks"] == "46000"
        assert payment["metadata"]["discount_kopecks"] == str((460 - expected) * 100)
        assert payment["metadata"].get("promo_code") == (None if global_wins else _CODE)
        assert await c.promo_redemption_repo.count_for_user(_CODE, iid) == 0
        events = c.identity._pool.bonus_events
        assert [event[2] for event in events] == ["bonus_claimed", "bonus_claimed", "plan_selected", "payment_created"]
        assert events[-1][4] == "test-broadcast-payment"
        # Обычная покупка не наследует последнюю рассылку.
        await click("do_pay:1m:7", 5)
        assert len(events) == 4
        # После истечения TTL/отсутствия получателя повторная бонусная кнопка не создаёт счёт.
        c.identity._pool.claim_row = None
        await click(button["callback_data"], 6)
        assert len(payments) == 2
        assert len(events) == 4

    _run(main())
