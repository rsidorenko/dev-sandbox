"""Provider-side tests for the Cloud.ru bare_xray gRPC integration: multi-transport link
building (tcp + xhttp) and the live lifecycle dispatch (add/remove via XrayApiClient)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.issuance.xui_client import XuiServerConfig
from app.issuance.xui_vless_provider import XuiVlessProvider, _vless_uuid_for_transport


def _bx_config(transport: str, port: int, tag: str, ws_path: str = "/bravada-xhttp") -> XuiServerConfig:
    return XuiServerConfig(
        server_id=26 if transport == "tcp" else 27,
        label=f"Cloud.ru {transport}",
        country_code="RU",
        country_flag="🇷🇺",
        server_host="85.208.87.187",
        server_port=port,
        ws_path=ws_path,
        tls_sni=None,
        panel_url="",
        panel_username="",
        panel_password="",
        inbound_id=0,
        reality_pbk="zHzFXnxH13f2lUXJZ3_Lqi3K9O6auoFjS84G57JLQD4",
        reality_sid="a4084380",
        reality_sni="cloudru.bravada-connect.online",
        reality_fp="firefox",
        transport_type=transport,
        provider_kind="bare_xray",
        xray_inbound_tag=tag,
        client_surface="happ",
    )


def _provider_with_bare_xray() -> XuiVlessProvider:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    p._key_state = AsyncMock(return_value={})
    p._bare_xray_configs = (_bx_config("tcp", 443, "cloudru-tcp"), _bx_config("xhttp", 8443, "cloudru-xhttp"))
    return p


# --- multi-transport link building ------------------------------------------


async def test_bare_xray_servers_emits_tcp_and_xhttp_links() -> None:
    p = _provider_with_bare_xray()
    uid = "u123456789"

    servers = await p._bare_xray_servers(uid)

    assert len(servers) == 2
    assert {s.surface for s in servers} == {"happ"}
    links = {s.server_label: s.vless_link for s in servers}

    tcp_link = links["Cloud.ru tcp"]
    xhttp_link = links["Cloud.ru xhttp"]

    # tcp link: per-transport tcp UUID, :443, type=tcp, flow=vision, fp=firefox
    tcp_uuid = _vless_uuid_for_transport(uid, "tcp")
    assert tcp_link.startswith(f"vless://{tcp_uuid}@85.208.87.187:443?")
    assert "type=tcp" in tcp_link and "flow=xtls-rprx-vision" in tcp_link and "fp=firefox" in tcp_link

    # xhttp link: DIFFERENT (xhttp) UUID, :8443, type=xhttp, no flow, fp=firefox, the path
    xhttp_uuid = _vless_uuid_for_transport(uid, "xhttp")
    assert xhttp_uuid != tcp_uuid  # distinct uuid per transport
    assert xhttp_link.startswith(f"vless://{xhttp_uuid}@85.208.87.187:8443?")
    assert "type=xhttp" in xhttp_link and "flow=" not in xhttp_link
    assert "path=%2Fbravada-xhttp" in xhttp_link
    # xhttp must pin packet-up (the fleet's xhttp inbounds run mode='auto', which accepts it;
    # packet-up multiplexes upstream over HTTP and is markedly faster than the stream default on
    # lossy RU links — same shape the CDN-bypass link_template already ships). `mode` is read by
    # sing-box clients, `extra={"mode":"packet-up"}` by xray-based ones (Karing).
    assert "mode=packet-up" in xhttp_link
    assert "extra=%7B%22mode%22%3A%22packet-up%22%7D" in xhttp_link


async def test_bare_xray_servers_empty_when_no_bare_xray() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    p._key_state = AsyncMock(return_value={})
    p._bare_xray_configs = ()
    assert await p._bare_xray_servers("u1") == []


# --- lifecycle dispatch -----------------------------------------------------


def _mock_client(transport: str, tag: str) -> AsyncMock:
    cfg = _bx_config(transport, 443 if transport == "tcp" else 8443, tag)
    m = AsyncMock()
    m.server_config = cfg
    m.server_id = cfg.server_id
    m.add_client = AsyncMock(return_value=True)
    m.remove_client = AsyncMock(return_value=True)
    return m


async def test_bare_xray_lifecycle_add_dispatches_per_transport() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    p._key_state = AsyncMock(return_value={})
    tcp = _mock_client("tcp", "cloudru-tcp")
    xhttp = _mock_client("xhttp", "cloudru-xhttp")
    p._bare_xray_clients = (tcp, xhttp)  # bypass env-driven _get_bare_xray_clients
    uid = "u999"

    await p._bare_xray_lifecycle(uid, "add")

    tcp.add_client.assert_awaited_once_with(user_uuid=_vless_uuid_for_transport(uid, "tcp"), email=f"user-{uid[:16]}")
    xhttp.add_client.assert_awaited_once_with(
        user_uuid=_vless_uuid_for_transport(uid, "xhttp"), email=f"x-user-{uid[:16]}"
    )
    tcp.remove_client.assert_not_awaited()
    xhttp.remove_client.assert_not_awaited()


async def test_bare_xray_lifecycle_remove_dispatches_per_transport() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    p._key_state = AsyncMock(return_value={})
    tcp = _mock_client("tcp", "cloudru-tcp")
    xhttp = _mock_client("xhttp", "cloudru-xhttp")
    p._bare_xray_clients = (tcp, xhttp)
    uid = "u999"

    await p._bare_xray_lifecycle(uid, "remove")

    tcp.remove_client.assert_awaited_once_with(email=f"user-{uid[:16]}")
    xhttp.remove_client.assert_awaited_once_with(email=f"x-user-{uid[:16]}")
    tcp.add_client.assert_not_awaited()
    xhttp.add_client.assert_not_awaited()


async def test_bare_xray_lifecycle_swallows_client_failure() -> None:
    """A failing bare_xray op must not crash the bot (reconcile re-applies)."""
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    p._key_state = AsyncMock(return_value={})
    bad = _mock_client("tcp", "cloudru-tcp")
    bad.add_client = AsyncMock(side_effect=RuntimeError("boom"))
    p._bare_xray_clients = (bad,)

    await p._bare_xray_lifecycle("u1", "add")  # must not raise


async def test_bare_xray_lifecycle_noop_without_clients() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    p._key_state = AsyncMock(return_value={})
    p._bare_xray_clients = ()  # nothing configured / creds unset
    await p._bare_xray_lifecycle("u1", "add")  # must not raise
