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


def _h1_mock(current_emails: list[str]) -> AsyncMock:
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
    m.get_inbound_clients = AsyncMock(return_value=[{"email": e} for e in current_emails])
    m.replace_inbound_clients = AsyncMock(return_value=True)
    m.restart_xray = AsyncMock(return_value=True)
    return m


async def test_reconcile_h1_diff_triggers_batch_update_and_restart() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    users = [{"internal_user_id": "u1"}, {"internal_user_id": "u2"}]
    m = _h1_mock(current_emails=["someone-else"])  # differs from desired
    await p._reconcile_h1_server(m, users)
    m.replace_inbound_clients.assert_awaited_once()
    desired = m.replace_inbound_clients.call_args.args[0]
    assert {c["email"] for c in desired} == {"x-user-u1", "x-user-u2"}
    assert all(c["id"] for c in desired)  # uuid set
    assert all("flow" not in c for c in desired)  # h1-schema
    assert all({"auth", "password", "subId", "security"} <= set(c) for c in desired)
    m.restart_xray.assert_awaited_once()


async def test_reconcile_h1_no_change_skips_update_and_restart() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    users = [{"internal_user_id": "u1"}]
    desired_email = _email_from_internal("u1", transport_type="xhttp")  # x-user-u1
    m = _h1_mock(current_emails=[desired_email])  # current == desired
    await p._reconcile_h1_server(m, users)
    m.replace_inbound_clients.assert_not_awaited()  # steady state
    m.restart_xray.assert_not_awaited()


async def test_reconcile_h1_update_failure_does_not_restart() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    users = [{"internal_user_id": "u1"}]
    m = _h1_mock(current_emails=[])
    m.replace_inbound_clients = AsyncMock(return_value=False)  # update failed
    await p._reconcile_h1_server(m, users)
    m.restart_xray.assert_not_awaited()  # don't restart after a failed update
