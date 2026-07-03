"""Tests for the Happ/Karing subscription split: UA-aware server selection in /sub/
and the provider's bare_xray (Cloud.ru) link builder."""

from __future__ import annotations

from app.issuance.vless_provider import VlessServerConfig
from app.issuance.xui_client import XuiServerConfig
from app.issuance.xui_vless_provider import (
    XuiVlessProvider,
    _build_vless_link,
    _vless_uuid_for_transport,
)
from app.web_api.subscription import _select_servers_for_client


def _server(label: str, surface: str = "default") -> VlessServerConfig:
    return VlessServerConfig(
        server_label=label,
        country_code="DE" if surface == "default" else "RU",
        country_flag="🇩🇪" if surface == "default" else "🇷🇺",
        vless_link=f"vless://x@{label}:443#{label}",
        surface=surface,
    )


# ---------------------------------------------------------------------------
# _select_servers_for_client
# ---------------------------------------------------------------------------


def test_happ_ua_gets_only_happ_surface() -> None:
    servers = (_server("Frankfurt"), _server("Helsinki"), _server("Cloud.ru", "happ"))
    selected = _select_servers_for_client(servers, ua="Happ/2.0 (Android)", client_param=None)
    assert [s.server_label for s in selected] == ["Cloud.ru"]


def test_non_happ_ua_gets_only_default_surface() -> None:
    servers = (_server("Frankfurt"), _server("Helsinki"), _server("Cloud.ru", "happ"))
    for ua in ("Karing/1.0", "v2rayN/7.0", "Mozilla/5.0 curl/8", ""):
        selected = _select_servers_for_client(servers, ua=ua, client_param=None)
        assert [s.server_label for s in selected] == ["Frankfurt", "Helsinki"]


def test_client_param_happ_forces_happ_set() -> None:
    servers = (_server("Frankfurt"), _server("Cloud.ru", "happ"))
    selected = _select_servers_for_client(servers, ua="something-else", client_param="happ")
    assert [s.server_label for s in selected] == ["Cloud.ru"]


def test_happ_falls_back_to_full_set_when_no_happ_configured() -> None:
    servers = (_server("Frankfurt"), _server("Helsinki"))
    selected = _select_servers_for_client(servers, ua="Happ/2.0", client_param=None)
    assert {s.server_label for s in selected} == {"Frankfurt", "Helsinki"}


def test_ua_match_is_case_insensitive() -> None:
    servers = (_server("Frankfurt"), _server("Cloud.ru", "happ"))
    assert _select_servers_for_client(servers, ua="hAPP/2.0", client_param=None)[0].surface == "happ"


# ---------------------------------------------------------------------------
# Provider: bare_xray (Cloud.ru) link building — surface + tcp/firefox link shape
# ---------------------------------------------------------------------------


def _cloudru_config() -> XuiServerConfig:
    return XuiServerConfig(
        server_id=26,
        label="Россия 🔄 (Happ)",
        country_code="RU",
        country_flag="🇷🇺",
        server_host="85.208.87.187",
        server_port=443,
        ws_path="/ws",
        tls_sni=None,
        panel_url="",
        panel_username="",
        panel_password="",
        inbound_id=0,
        reality_pbk="zHzFXnxH13f2lUXJZ3_Lqi3K9O6auoFjS84G57JLQD4",
        reality_sid="a4084380",
        reality_sni="cloudru.bravada-connect.online",
        reality_fp="firefox",
        transport_type="tcp",
        provider_kind="bare_xray",
    )


async def test_bare_xray_servers_tagged_happ_with_correct_link() -> None:
    provider = XuiVlessProvider(pool=None)  # type: ignore[arg-type]  # _bare_xray_servers doesn't touch the pool
    provider._bare_xray_configs = (_cloudru_config(),)
    uid = "u123456789"

    servers = await provider._bare_xray_servers(uid)

    assert len(servers) == 1
    link = servers[0].vless_link
    expected_uuid = _vless_uuid_for_transport(uid, "tcp")
    assert link.startswith(f"vless://{expected_uuid}@85.208.87.187:443?")
    assert "type=tcp" in link
    assert "security=reality" in link
    assert "fp=firefox" in link
    assert "sni=cloudru.bravada-connect.online" in link
    assert "sid=a4084380" in link
    assert "flow=xtls-rprx-vision" in link
    assert servers[0].surface == "happ"


async def test_bare_xray_servers_empty_when_none_configured() -> None:
    provider = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    provider._bare_xray_configs = ()
    assert await provider._bare_xray_servers("u1") == []


def test_build_vless_link_tcp_uses_row_reality_fp() -> None:
    """The tcp branch must honour the row's reality_fp (firefox for Cloud.ru, chrome
    for the fleet) — this is what makes the Happ link use fp=firefox without a template."""
    link = _build_vless_link(_cloudru_config(), "uuid-x")
    assert "fp=firefox" in link
