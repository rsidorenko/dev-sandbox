"""Tests for XuiApiClient v3-panel behavior: delete + add via read-modify-write.

v3 3x-ui panels return 404 for delClient/addClient. The client must fall back
to read-modify-write via /panel/api/inbounds/update/{id}, and add must upsert
by email so re-adds after a failed delete don't create duplicate-email clients.
"""

from __future__ import annotations

import json

import httpx

from app.issuance.xui_client import XuiApiClient, XuiServerConfig, XuiOutcome
from app.shared.test_helpers import run_async as _run


def _config(inbound_id: int = 7) -> XuiServerConfig:
    return XuiServerConfig(
        server_id=10,
        label="test-server",
        country_code="DE",
        country_flag="x",
        server_host="203.0.113.10",
        server_port=443,
        ws_path="/ws",
        tls_sni=None,
        panel_url="https://lte.example",
        panel_username="bravada",
        panel_password="secret",
        inbound_id=inbound_id,
        transport_type="tcp",
    )


class _Panel:
    """Minimal v3 panel simulator backed by an in-memory inbound."""

    def __init__(self, inbound_id: int, clients: list[dict]) -> None:
        self.inbound_id = inbound_id
        self.clients = clients
        self.update_calls: list[dict] = []
        self.del_client_calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        if path == "/login":
            return httpx.Response(200, json={"success": True})
        if path == "/csrf-token":
            return httpx.Response(200, json={"obj": "csrftoken"})
        if path == "/panel/api/inbounds/get/7":
            settings = json.dumps({"clients": list(self.clients)})
            return httpx.Response(200, json={"success": True, "obj": {
                "id": 7, "port": 443, "protocol": "vless", "tag": "in-443-tcp",
                "remark": "r", "enable": True, "listen": "", "expiryTime": 0,
                "total": 0, "up": 0, "down": 0,
                "settings": settings,
                "streamSettings": {"network": "tcp"},
                "sniffing": {"enabled": True},
            }})
        if "/panel/api/inbounds/7/delClient/" in path:
            uuid = path.rsplit("/", 1)[-1]
            self.del_client_calls.append(uuid)
            return httpx.Response(404)  # v3 panels 404 on delClient
        if path == "/panel/api/inbounds/addClient":
            return httpx.Response(404)  # v3 panels 404 on addClient
        if path == "/panel/api/inbounds/update/7":
            body = json.loads(request.content.decode())
            self.clients = json.loads(body["settings"]).get("clients", [])
            self.update_calls.append(body)
            return httpx.Response(200, json={"success": True, "msg": "updated"})
        if path == "/panel/api/inbounds/getClientTraffics/" + url.rsplit("/", 1)[-1]:
            return httpx.Response(200, json={"success": False})  # triggers v3 resolve
        return httpx.Response(404)


def _client(panel: _Panel) -> XuiApiClient:
    c = XuiApiClient(_config())
    transport = httpx.MockTransport(panel.handler)
    c._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
    return c


def test_delete_falls_back_to_update_on_v3_panel():
    """delClient 404 -> client removed via read-modify-write update."""
    panel = _Panel(7, [{"id": "uuid-old", "email": "user-abc"}])
    c = _client(panel)
    result = _run(c.delete_client(user_uuid="uuid-old"))
    assert result.outcome == XuiOutcome.SUCCESS
    assert panel.del_client_calls == ["uuid-old"]  # tried delClient first
    assert len(panel.update_calls) == 1
    # client removed in the written settings
    written = json.loads(panel.update_calls[0]["settings"])
    assert all(cl.get("id") != "uuid-old" for cl in written["clients"])


def test_add_upsert_replaces_same_email():
    """_add_client_via_update replaces an existing same-email client (no duplicate)."""
    panel = _Panel(7, [{"id": "uuid-old", "email": "user-abc"}])
    c = _client(panel)
    c._v3_mode = True  # type: ignore[attr-defined]
    new_settings = {"id": "uuid-new", "email": "user-abc", "enable": True,
                    "expiryTime": 0, "flow": "", "limitIp": 0, "totalGB": 0,
                    "tgId": "", "subId": ""}
    result = _run(c.add_client(user_uuid="uuid-new", email="user-abc", expiry_ts=0))
    assert result.outcome == XuiOutcome.SUCCESS
    assert len(panel.clients) == 1  # replaced, not appended
    assert panel.clients[0]["id"] == "uuid-new"
    assert panel.clients[0]["email"] == "user-abc"


def test_delete_then_add_no_duplicate():
    """Full v3 re-add cycle: delete removes old, add inserts new (1 client, no dup)."""
    panel = _Panel(7, [{"id": "uuid-old", "email": "user-abc"}])
    c = _client(panel)
    # delete (v3 fallback) then add (v3 upsert)
    _run(c.delete_client(user_uuid="uuid-old"))
    result = _run(c.add_client(user_uuid="uuid-new", email="user-abc", expiry_ts=0))
    assert result.outcome == XuiOutcome.SUCCESS
    assert len(panel.clients) == 1
    assert panel.clients[0]["id"] == "uuid-new"


def test_add_appends_when_no_match():
    """Upsert appends when no same-email/id client exists."""
    panel = _Panel(7, [{"id": "uuid-a", "email": "user-a"}])
    c = _client(panel)
    c._v3_mode = True  # type: ignore[attr-defined]
    result = _run(c.add_client(user_uuid="uuid-b", email="user-b", expiry_ts=0))
    assert result.outcome == XuiOutcome.SUCCESS
    assert len(panel.clients) == 2
