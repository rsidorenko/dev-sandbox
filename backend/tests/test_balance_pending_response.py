"""Принятая оплата не отображается как ошибка, требующая нового списания."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.balance_payment_service import BalancePaymentResult, BalancePaymentStatus, pay_plan_from_balance
from app.bot_transport.runtime_facade import _map_balance_payment_result
from app.domain.plans import get_plan
from app.web_api.payment import _balance_result_to_response


async def test_pending_payment_is_accepted_in_web_and_bot():
    result = BalancePaymentResult(BalancePaymentStatus.PROVISIONING, "payer", "operation", amount_kopecks=24900)
    response = _balance_result_to_response(result)
    body = json.loads(response.body)
    assert response.status_code == 202
    assert body["ok"] is True and body["status"] == "provisioning"
    assert body["amount_kopecks"] == 24900
    assert "Повторно платить не нужно" in body["message"]
    text, _keyboard = await _map_balance_payment_result(result, None, None)
    assert "Оплата принята" in text and "Повторно платить не нужно" in text


@pytest.mark.parametrize("replayed", [False, True])
async def test_new_pending_purchase_consumes_promo_but_replay_does_not(monkeypatch, replayed):
    result = BalancePaymentResult(BalancePaymentStatus.PROVISIONING, "payer", "operation", replayed=replayed)
    monkeypatch.setattr(
        "app.application.balance_payment_service.get_plan_pricing",
        lambda pool: SimpleNamespace(get_plan=AsyncMock(return_value=get_plan("1m"))),
    )
    monkeypatch.setattr(
        "app.application.balance_payment_service._resolve_balance_discount",
        AsyncMock(return_value=(object(), 24900, True)),
    )
    monkeypatch.setattr("app.application.balance_payment_service._pay_plan_pg", AsyncMock(return_value=result))
    redeem = AsyncMock()
    monkeypatch.setattr("app.application.balance_payment_service._redeem_balance_promo", redeem)
    await pay_plan_from_balance(pool=object(), composition=None, internal_user_id="payer", plan_id="1m", device_count=5)
    assert redeem.await_count == (0 if replayed else 1)
