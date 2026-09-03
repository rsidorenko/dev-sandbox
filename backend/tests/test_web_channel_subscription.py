"""Юнит-тесты строгой проверки подписки для Web API."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request

from app.shared.test_helpers import run_async as _run
from app.web_api.channel_subscription import (
    CHANNEL_SUBSCRIPTION_REQUIRED_ERROR,
    require_channel_subscription,
)


class _FakeGate:
    def __init__(self, result: bool | None) -> None:
        self.result = result
        self.calls: list[int] = []

    async def is_member(self, user_id: int) -> bool | None:
        self.calls.append(user_id)
        return self.result


def _request(*, required: bool, checker: _FakeGate | None) -> Request:
    app = Starlette()
    app.state.channel_subscription_required = required
    app.state.channel_membership_checker = checker
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/user/promocode/apply",
            "headers": [],
            "app": app,
        }
    )


def test_web_gate_off_allows_benefit_without_check() -> None:
    gate = _FakeGate(False)
    result = _run(require_channel_subscription(_request(required=False, checker=gate), 42))
    assert result is None
    assert gate.calls == []


def test_web_gate_allows_only_confirmed_member() -> None:
    gate = _FakeGate(True)
    result = _run(require_channel_subscription(_request(required=True, checker=gate), 42))
    assert result is None
    assert gate.calls == [42]


def test_web_gate_rejects_unsubscribed_unknown_and_web_only_accounts() -> None:
    for telegram_user_id, gate_result, expected_calls in ((42, False, [42]), (42, None, [42]), (-42, True, [])):
        gate = _FakeGate(gate_result)
        result = _run(require_channel_subscription(_request(required=True, checker=gate), telegram_user_id))
        assert result is not None
        assert result.status_code == 403
        assert result.body and CHANNEL_SUBSCRIPTION_REQUIRED_ERROR.encode() in result.body
        assert gate.calls == expected_calls


def test_web_trial_does_not_touch_database_until_subscription_is_confirmed(monkeypatch) -> None:
    """Профильный endpoint проверяет канал до атомарного claim пробного периода."""

    class _PoolMustNotBeUsed:
        async def fetchrow(self, *args, **kwargs):  # pragma: no cover - вызов означает регрессию
            raise AssertionError("trial claim must not reach the database")

    async def _auth(_request: Request) -> dict[str, int]:
        return {"telegram_user_id": 42}

    monkeypatch.setattr("app.web_api.profile.require_auth", _auth)
    request = _request(required=True, checker=_FakeGate(False))
    request.app.state.pool = _PoolMustNotBeUsed()

    from app.web_api.profile import handle_activate_trial

    result = _run(handle_activate_trial(request))
    assert result.status_code == 403
    assert CHANNEL_SUBSCRIPTION_REQUIRED_ERROR.encode() in result.body
