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

from app.application.bootstrap import build_slice1_composition
from app.application.telegram_command_rate_limit import InMemoryTelegramCommandRateLimiter
from app.bot_transport.runtime_facade import (
    _broadcast_bonus_unavailable_text,
    _handle_broadcast_promo_claim,
    handle_slice1_telegram_update_to_rendered_message,
)
from app.domain.promo import PromoAudience, PromoCode, PromoDiscountKind
from app.persistence.in_memory import InMemoryPromoAppliedRepository, InMemoryPromoCodeRepository
from app.persistence.promo_contracts import PromoAppliedRecord
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run

_JOB_ID = "11111111-2222-3333-4444-555555555555"
_CODE = "SUMMER25"


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

    async def fetchrow(self, sql: str, *args):
        if "broadcast_promo_claims" in sql:
            return self.claim_row
        return None


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
    assert "24 часа" in text


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
