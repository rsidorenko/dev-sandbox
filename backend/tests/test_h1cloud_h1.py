"""Tests for the h1cloud h1-fork integration: the h1 client schema (build_h1_client) and the
batch desired-state reconcile (_reconcile_h1_server: diff → update + restart; no-change → skip)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.issuance.xui_client import XuiServerConfig
from app.issuance.xui_vless_provider import (
    XuiVlessProvider,
    _email_from_internal,
    build_h1_client,
)


def test_build_h1_client_shape() -> None:
    c = build_h1_client(user_uuid="the-uuid", email="x-user-u1")
    assert c["id"] == "the-uuid"
    assert c["email"] == "x-user-u1"
    assert c["enable"] is True
    assert c["expiryTime"] == 0 and c["limitIp"] == 0 and c["totalGB"] == 0
    assert c["tgId"] == 0  # numeric (not the standard 3x-ui "")
    assert "flow" not in c  # h1-fork: NO flow field
    assert c["security"] == "auto"
    assert c["comment"] == "" and c["reset"] == 0
    for f in ("subId", "auth", "password"):
        v = c[f]
        assert isinstance(v, str) and len(v) == 16 and v.isalnum(), f"{f} not 16 lowercase-alnum: {v!r}"


def test_build_h1_client_unique_tokens() -> None:
    a = build_h1_client(user_uuid="U", email="e")
    b = build_h1_client(user_uuid="U", email="e")
    # tokens generated per call (not fixed)
    assert a["subId"] != b["subId"]
    assert a["auth"] != b["auth"]
    assert a["password"] != b["password"]


def test_build_h1_client_enable_param_drives_enable_field() -> None:
    """enable mirrors the subscription state — expired users must be disabled on h1cloud
    (the fork uses expiryTime=0, so an enable=True expired user = infinite key leak)."""
    assert build_h1_client(user_uuid="U", email="e")["enable"] is True  # default
    assert build_h1_client(user_uuid="U", email="e", enable=False)["enable"] is False
    assert build_h1_client(user_uuid="U", email="e", enable=True)["enable"] is True


def _h1_mock(current: list[dict]) -> AsyncMock:
    cfg = XuiServerConfig(
        server_id=22,
        label="h1cloud",
        country_code="NL",
        country_flag="🇳🇱",
        server_host="node67.safetunn.shop",
        server_port=443,
        ws_path="api/v1/bs1961/sync",
        tls_sni="node67.safetunn.shop",
        panel_url="http://193.233.126.96:25100/x",
        panel_username="u",
        panel_password="p",
        inbound_id=1,
        transport_type="xhttp",
        provider_kind="xui",
        client_surface="happ",
        panel_flavor="h1",
    )
    m = AsyncMock()
    m.server_config = cfg
    m.server_id = 22
    m.get_inbound_clients = AsyncMock(return_value=current)
    m.replace_inbound_clients = AsyncMock(return_value=True)
    m.restart_xray = AsyncMock(return_value=True)
    return m


async def test_reconcile_h1_diff_triggers_batch_update_and_restart() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    users = [{"internal_user_id": "u1", "state_label": "active"}, {"internal_user_id": "u2", "state_label": "active"}]
    m = _h1_mock(current=[{"email": "someone-else", "enable": True}])  # email set differs
    await p._reconcile_h1_server(m, users)
    m.replace_inbound_clients.assert_awaited_once()
    desired = m.replace_inbound_clients.call_args.args[0]
    assert {c["email"] for c in desired} == {"x-user-u1", "x-user-u2"}
    assert all(c["id"] for c in desired)  # uuid set
    assert all("flow" not in c for c in desired)  # h1-schema
    assert all({"auth", "password", "subId", "security"} <= set(c) for c in desired)
    assert all(c["enable"] is True for c in desired)  # active users enabled
    m.restart_xray.assert_awaited_once()


async def test_reconcile_h1_disables_expired_users() -> None:
    """Regression: expired/in-grace users had enable=True on h1cloud (build_h1_client
    hardcoded it + expiryTime=0) → infinite key. Reconcile must set enable from state_label."""
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    users = [{"internal_user_id": "u1", "state_label": "active"}, {"internal_user_id": "u2", "state_label": "expired"}]
    m = _h1_mock(current=[])  # forces a rebuild
    await p._reconcile_h1_server(m, users)
    desired = {c["email"]: c for c in m.replace_inbound_clients.call_args.args[0]}
    assert desired["x-user-u1"]["enable"] is True  # active
    assert desired["x-user-u2"]["enable"] is False  # expired → disabled (was the leak)


async def test_reconcile_h1_no_change_skips_update_and_restart() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    users = [{"internal_user_id": "u1", "state_label": "active"}]
    desired_email = _email_from_internal("u1", transport_type="xhttp")  # x-user-u1
    m = _h1_mock(current=[{"email": desired_email, "enable": True}])  # emails AND enable match
    await p._reconcile_h1_server(m, users)
    m.replace_inbound_clients.assert_not_awaited()  # steady state
    m.restart_xray.assert_not_awaited()


async def test_reconcile_h1_enable_mismatch_triggers_rebuild() -> None:
    """A user who expired (state_label flipped, email set unchanged) must still get rebuilt —
    the h1-fork uses expiryTime=0, so without flipping enable=False the key stays live. The
    reconcile rebuilds on enable-mismatch, not just email-set change."""
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    users = [{"internal_user_id": "u1", "state_label": "expired"}]  # desired enable=False
    desired_email = _email_from_internal("u1", transport_type="xhttp")
    m = _h1_mock(current=[{"email": desired_email, "enable": True}])  # same email, wrong enable
    await p._reconcile_h1_server(m, users)
    m.replace_inbound_clients.assert_awaited_once()  # rebuild despite same email set
    desired = m.replace_inbound_clients.call_args.args[0]
    assert desired[0]["enable"] is False
    m.restart_xray.assert_awaited_once()


async def test_reconcile_h1_update_failure_does_not_restart() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    users = [{"internal_user_id": "u1"}]
    m = _h1_mock(current=[])
    m.replace_inbound_clients = AsyncMock(return_value=False)  # update failed
    await p._reconcile_h1_server(m, users)
    m.restart_xray.assert_not_awaited()  # don't restart after a failed update
