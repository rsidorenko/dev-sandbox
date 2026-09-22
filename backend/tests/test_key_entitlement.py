"""HTTP-операции ключей соблюдают право на доступ и параметры подписки."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderResult, VlessUserConfig
from app.web_api.profile import handle_get_keys, handle_reissue_keys


def _request(row):
    async def fetchrow(sql, *args):
        if "subscription_snapshots" in sql:
            return row
        return {"internal_user_id": "entitlement-test", "vless_uuid": "old"}

    pool = AsyncMock()
    pool.fetchrow.side_effect = fetchrow
    provider = AsyncMock()
    provider.revoke_user.return_value = VlessProviderResult(VlessProviderOutcome.SUCCESS)
    provider.create_user.return_value = VlessProviderResult(
        VlessProviderOutcome.SUCCESS, VlessUserConfig("new", "subscription", ())
    )
    provider.get_user_config.return_value = VlessProviderResult(VlessProviderOutcome.UNAVAILABLE)
    app = SimpleNamespace(state=SimpleNamespace(pool=pool, vless_provider=provider))
    request = Request({"type": "http", "app": app, "headers": []})
    request.state.user = {"telegram_user_id": 123}
    return request, provider


def _snapshot(**overrides):
    return {
        "state_label": "active",
        "active_until_utc": datetime.now(UTC) + timedelta(hours=3),
        "trial_expires_at": None,
        "keys_deleted_at": None,
        "device_count": 9,
        **overrides,
    }


@pytest.mark.parametrize("handler", [handle_get_keys, handle_reissue_keys])
@pytest.mark.parametrize("state", ["missing", "expired", "inactive", "deleted"])
async def test_no_access_means_no_provider_calls(handler, state):
    row = _snapshot()
    if state == "missing":
        row = None
    elif state == "expired":
        row["active_until_utc"] = datetime.now(UTC) - timedelta(seconds=1)
    elif state == "inactive":
        row["state_label"] = "inactive"
    else:
        row["keys_deleted_at"] = datetime.now(UTC)
    request, provider = _request(row)
    response = await handler(request)
    assert response.status_code == 403
    assert not provider.mock_calls


async def test_get_keys_does_not_create_on_provider_failure():
    request, provider = _request(_snapshot())
    await handle_get_keys(request)
    provider.create_user.assert_not_awaited()
    provider.get_user_config.assert_awaited_once()


@pytest.mark.parametrize("trial", [False, True])
async def test_reissue_preserves_exact_entitlement(trial):
    row = _snapshot()
    if trial:
        row["trial_expires_at"] = row["active_until_utc"]
        row["active_until_utc"] = None
    deadline = row["trial_expires_at"] if trial else row["active_until_utc"]
    request, provider = _request(row)
    response = await handle_reissue_keys(request)
    assert response.status_code == 200
    provider.create_user.assert_awaited_once_with(
        internal_user_id="entitlement-test", device_count=9, expires_at=deadline
    )


async def test_real_provider_passes_exact_expiry_to_panel(monkeypatch):
    from app.issuance.xui_client import XuiClientResult, XuiOutcome
    from app.issuance.xui_vless_provider import XuiVlessProvider

    client = SimpleNamespace(
        server_id=1,
        server_config=SimpleNamespace(transport_type="tcp", panel_url="https://panel.test"),
        resolve_client_uuid=AsyncMock(return_value=None),
        add_client=AsyncMock(return_value=XuiClientResult(XuiOutcome.UNAVAILABLE)),
    )
    provider = XuiVlessProvider(pool=AsyncMock())
    monkeypatch.setattr(provider, "_get_clients", AsyncMock(return_value=[client]))
    deadline = datetime.now(UTC) + timedelta(minutes=47)
    await provider.create_user(internal_user_id="exact-test", device_count=11, expires_at=deadline)
    assert client.add_client.await_args.kwargs["expiry_ts"] == int(deadline.timestamp() * 1000)
    assert client.add_client.await_args.kwargs["limit_ip"] == 11


@pytest.mark.parametrize("active", [False, True])
async def test_telegram_reissue_checks_entitlement(active):
    from app.application.bootstrap import build_slice1_composition
    from app.application.interfaces import SubscriptionSnapshot
    from app.bot_transport.runtime_facade import handle_slice1_telegram_update_to_rendered_message
    from app.issuance.vless_provider import StubVlessProvider

    provider = StubVlessProvider()
    provider.create_user = AsyncMock(wraps=provider.create_user)
    provider.revoke_user = AsyncMock(wraps=provider.revoke_user)
    composition = build_slice1_composition(bot_username="testbot", vless_provider=provider)
    await composition.identity.create_if_absent(7801)
    identity = await composition.identity.find_by_telegram_user_id(7801)
    deadline = datetime.now(UTC) + timedelta(hours=2 if active else -2)
    await composition.snapshots.upsert_state(
        SubscriptionSnapshot(identity.internal_user_id, "active", active_until_utc=deadline, device_count=8)
    )
    await handle_slice1_telegram_update_to_rendered_message(
        {
            "update_id": 7801,
            "callback_query": {
                "id": "reissue-test",
                "from": {"id": 7801},
                "message": {"message_id": 1, "chat": {"id": 7801}},
                "data": "reissue_keys_confirm",
            },
        },
        composition,
    )
    if active:
        provider.create_user.assert_awaited_once_with(
            internal_user_id=identity.internal_user_id, device_count=8, expires_at=deadline
        )
    else:
        provider.create_user.assert_not_awaited()
        provider.revoke_user.assert_not_awaited()
