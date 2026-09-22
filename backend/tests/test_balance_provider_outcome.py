"""Типизированный отказ настоящего провайдера не считается успешной выдачей."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.balance_payment_service import _ensure_vless_keys_atomic
from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderResult
from app.issuance.xui_vless_provider import XuiVlessProvider


def _pool(monkeypatch):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    conn = pool.acquire.return_value.__aenter__.return_value
    conn.transaction = MagicMock()
    conn.execute = AsyncMock()
    credit = AsyncMock()
    monkeypatch.setattr(
        "app.application.balance_payment_service.PostgresReferralBalanceRepository.credit_in_connection", credit
    )
    return pool, credit


async def _attempt(pool, provider):
    return await _ensure_vless_keys_atomic(
        pool=pool,
        vless_provider=provider,
        internal_user_id="test-outcome",
        device_count=5,
        pre_payment_snap=None,
        total_kopecks=24900,
        plan_id="1m",
        idempotency_key="outcome-test",
        correlation_id="outcome-test",
    )


@pytest.mark.parametrize("deactivated", [False, True])
async def test_unavailable_result_retries_and_compensates(monkeypatch, deactivated):
    pool, credit = _pool(monkeypatch)
    pool.fetchrow.return_value = {"keys_deleted_at": None, "keys_deactivated_at": 1 if deactivated else None}
    provider = AsyncMock()
    operation = provider.activate_user if deactivated else provider.create_user
    operation.return_value = VlessProviderResult(VlessProviderOutcome.UNAVAILABLE)
    monkeypatch.setattr("app.application.balance_payment_service.asyncio.sleep", AsyncMock())

    assert await _attempt(pool, provider) is True
    assert operation.await_count == 3
    pool.execute.assert_not_awaited()
    credit.assert_awaited_once()


async def test_real_provider_without_servers_does_not_report_success(monkeypatch):
    pool, credit = _pool(monkeypatch)
    pool.fetchrow.return_value = None
    provider = XuiVlessProvider(pool=pool)
    monkeypatch.setattr(provider, "_get_clients", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.application.balance_payment_service.asyncio.sleep", AsyncMock())

    assert await _attempt(pool, provider) is True
    pool.execute.assert_not_awaited()
    credit.assert_awaited_once()


async def test_temporary_failure_recovers_before_compensation(monkeypatch):
    pool, credit = _pool(monkeypatch)
    pool.fetchrow.return_value = None
    provider = AsyncMock()
    provider.create_user.side_effect = [
        VlessProviderResult(VlessProviderOutcome.UNAVAILABLE),
        VlessProviderResult(VlessProviderOutcome.SUCCESS),
    ]
    monkeypatch.setattr("app.application.balance_payment_service.asyncio.sleep", AsyncMock())

    assert await _attempt(pool, provider) is False
    assert provider.create_user.await_count == 2
    pool.execute.assert_awaited_once()
    credit.assert_not_awaited()
