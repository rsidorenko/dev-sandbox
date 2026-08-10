"""Юнит-тесты обработчика бонус-кнопки рассылки ``_handle_broadcast_days_claim``.

Покрываем все пути: отказы (uid/identity/pool/rate-limit/UPDATE 0 — единый текст) и успех
(claim→grant→закрытие trial_used; ветвление текста по provisioning). Composition — реальная
in-memory (как в test_bot_transport_runtime_facade), пул — локальный фейк, grant — monkeypatch.

Идемпотентность и «только получатели» гарантируются atomic UPDATE eligible→claimed в
broadcast_bonus_claims (WHERE status='eligible' AND expires_at>NOW() RETURNING days): 0 строк
для всех причин отказа → единый текст. Здесь это моделируется возвратом None из fetchrow.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from app.application.bootstrap import build_slice1_composition
from app.application.subscription_grant import GrantResult
from app.application.telegram_command_rate_limit import InMemoryTelegramCommandRateLimiter
from app.bot_transport.runtime_facade import (
    _broadcast_bonus_unavailable_text,
    _handle_broadcast_days_claim,
    handle_slice1_telegram_update_to_rendered_message,
)
from app.issuance.vless_provider import StubVlessProvider
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run

_JOB_ID = "11111111-2222-3333-4444-555555555555"


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


async def _register(c, *, uid: int = 42) -> None:
    await handle_slice1_telegram_update_to_rendered_message(
        _start_update(uid=uid), c, correlation_id=new_correlation_id()
    )


class _ClaimPool:
    """Фейк-пул для обработчика: fetchrow обслуживает atomic claim (RETURNING days),
    execute — закрытие trial_used. grant мокается отдельно."""

    def __init__(self, *, claim_row: dict | None = None) -> None:
        self.claim_row = claim_row
        self.trial_used_updates = 0

    async def fetchrow(self, sql: str, *args):
        if "RETURNING DAYS" in sql.upper():
            return self.claim_row
        return None

    async def execute(self, sql: str, *args):
        if "TRIAL_USED" in sql.upper():
            self.trial_used_updates += 1
        return "UPDATE 1"


def test_claim_refuses_when_uid_none() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        text, _kb = await _handle_broadcast_days_claim(c, None, _JOB_ID)
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_claim_refuses_when_job_id_empty() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        text, _kb = await _handle_broadcast_days_claim(c, 42, "")
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_claim_refuses_when_rate_limited() -> None:
    """Soft anti-spam: лимитер блокит спам-клики до обращения к БД."""

    async def main() -> None:
        c = dataclasses.replace(
            build_slice1_composition(),
            command_rate_limiter=InMemoryTelegramCommandRateLimiter(broadcast_claim_limit=0, now_seconds=lambda: 100.0),
        )
        await _register(c, uid=42)
        text, _kb = await _handle_broadcast_days_claim(c, 42, _JOB_ID)
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_claim_refuses_when_identity_not_found() -> None:
    """Юзер не зарегистрирован → нет identity → отказ тем же текстом."""

    async def main() -> None:
        c = build_slice1_composition()
        # не регистрируем uid=77
        text, _kb = await _handle_broadcast_days_claim(c, 77, _JOB_ID)
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_claim_refuses_when_pool_missing() -> None:
    """In-memory composition без postgres-пула → отказ (бонус требует БД)."""

    async def main() -> None:
        c = build_slice1_composition()
        await _register(c, uid=42)
        text, _kb = await _handle_broadcast_days_claim(c, 42, _JOB_ID)
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_claim_refuses_when_no_eligible_row() -> None:
    """UPDATE 0 — единый отказ для всех причин: уже claimed / не получатель / TTL истёк."""

    async def main() -> None:
        c = build_slice1_composition()
        await _register(c, uid=42)
        c.identity._pool = _ClaimPool(claim_row=None)  # type: ignore[attr-defined]
        text, _kb = await _handle_broadcast_days_claim(c, 42, _JOB_ID)
        assert text == _broadcast_bonus_unavailable_text()

    _run(main())


def test_claim_grants_days_and_closes_trial_on_success(monkeypatch) -> None:
    """Успех: claim выигран → grant вызван с правильными аргументами, trial закрыт,
    текст про активацию бонуса и готовые ключи."""
    grant_calls: list[dict] = []

    async def _fake_grant(**kw):
        grant_calls.append(kw)
        return GrantResult(
            created=True,
            days=kw["days"],
            old_active_until=None,
            new_active_until=datetime(2026, 1, 15, tzinfo=UTC),
            plan_id="custom:5",
            device_count=5,
            provisioning="success servers=3",
            servers_ok=3,
            correlation_id=kw.get("correlation_id") or "cid",
        )

    monkeypatch.setattr("app.application.subscription_grant.grant_subscription_days", _fake_grant)

    async def main() -> None:
        c = build_slice1_composition(vless_provider=StubVlessProvider())
        await _register(c, uid=42)
        pool = _ClaimPool(claim_row={"days": 5})
        c.identity._pool = pool  # type: ignore[attr-defined]
        text, kb = await _handle_broadcast_days_claim(c, 42, _JOB_ID)
        return text, kb, pool

    text, _kb, pool = _run(main())

    assert "+5 дн. бесплатно" in text
    assert "2026-01-15" in text  # new_active_until.date()
    # grant вызван ровно один раз с ожидаемыми аргументами
    assert len(grant_calls) == 1
    kw = grant_calls[0]
    assert kw["days"] == 5
    assert kw["actor"] == f"broadcast_claim:{_JOB_ID}"
    assert kw["correlation_id"] == f"bc_claim:{_JOB_ID[:8]}:42"
    assert kw["internal_user_id"]
    # триал закрыт, чтобы бонус не был перезаписан последующей активацией триала
    assert pool.trial_used_updates == 1


def test_claim_success_text_when_provisioning_skipped(monkeypatch) -> None:
    """VLESS-провайдер отсутствует/сбойнул — claim уже claimed, снапшот продлён, ключи
    добьёт reconcile-таймер. Текст ветвится на «ключи будут готовы»."""

    async def _fake_grant(**kw):
        return GrantResult(
            created=False,
            days=kw["days"],
            old_active_until=None,
            new_active_until=datetime(2026, 2, 1, tzinfo=UTC),
            plan_id="1m",
            device_count=3,
            provisioning="skipped_no_provider",
            servers_ok=0,
            correlation_id="cid",
        )

    monkeypatch.setattr("app.application.subscription_grant.grant_subscription_days", _fake_grant)

    async def main() -> None:
        c = build_slice1_composition()  # без vless_provider
        await _register(c, uid=42)
        c.identity._pool = _ClaimPool(claim_row={"days": 3})  # type: ignore[attr-defined]
        return await _handle_broadcast_days_claim(c, 42, _JOB_ID)

    text, _kb = _run(main())
    assert "+3 дн. бесплатно" in text
    assert "Ключи будут готовы" in text
    assert "2026-02-01" in text


def test_claim_rate_limiter_fail_open_on_exception(monkeypatch) -> None:
    """Сбой лимитера не должен блокировать выдачу бонуса (fail-open)."""
    monkeypatch.setattr("app.application.subscription_grant.grant_subscription_days", _noop_grant)

    class _BoomLimiter:
        async def allow(self, *, telegram_user_id, command_key):
            raise RuntimeError("limiter down")

    async def main() -> None:
        c = dataclasses.replace(build_slice1_composition(), command_rate_limiter=_BoomLimiter())
        await _register(c, uid=42)
        c.identity._pool = _ClaimPool(claim_row={"days": 3})  # type: ignore[attr-defined]
        return await _handle_broadcast_days_claim(c, 42, _JOB_ID)

    text, _kb = _run(main())
    # fail-open: пошли дальше лимитера и дошли до grant → текст активации, не отказ
    assert text != _broadcast_bonus_unavailable_text()
    assert "+3 дн. бесплатно" in text


def test_callback_bc_days_routes_through_pipeline_to_handler() -> None:
    """Регресс routing: callback 'bc_days:{job_id}' проходит gate+dispatcher до обработчика.
    In-memory composition без postgres-пула → единый текст отказа (как во всех путях отказа)."""

    async def main() -> None:
        c = build_slice1_composition()
        await _register(c, uid=42)
        raw = {
            "update_id": 2,
            "callback_query": {"id": "cq1", "from": {"id": 42}, "data": f"bc_days:{_JOB_ID}"},
        }
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=new_correlation_id())
        return pkg.message_text

    assert _run(main()) == _broadcast_bonus_unavailable_text()


async def _noop_grant(**kw):
    return GrantResult(
        created=True,
        days=kw["days"],
        old_active_until=None,
        new_active_until=datetime(2026, 3, 1, tzinfo=UTC),
        plan_id="custom:3",
        device_count=5,
        provisioning="success servers=1",
        servers_ok=1,
        correlation_id="cid",
    )
