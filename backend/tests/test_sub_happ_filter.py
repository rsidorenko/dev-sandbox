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
        client_surface="happ",
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


def _cdn_config() -> XuiServerConfig:
    return XuiServerConfig(
        server_id=22,
        label="Обход белых списков",
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
        link_template="vless://{UUID}@node67.safetunn.shop:443?extra=OBF&type=xhttp#bypass",
        provider_kind="xui",
        client_surface="happ",
    )


def test_cdn_bypass_link_template_emitted_verbatim_per_user() -> None:
    """The h1cloud CDN bypass row (link_template, client_surface='happ') emits the full
    obfuscated CDN vless:// verbatim with {UUID} -> per-user uuid (the `extra` obfuscation
    is fixed). It's an Xray-core/Happ-only key, so its surface is 'happ'."""
    link = _build_vless_link(_cdn_config(), "user-uuid-123")
    assert link == "vless://user-uuid-123@node67.safetunn.shop:443?extra=OBF&type=xhttp#bypass"
    assert "extra=OBF" in link  # obfuscation preserved verbatim


def test_cdn_bypass_surface_routes_to_happ_only() -> None:
    """A xui row with client_surface='happ' (the CDN bypass) is served to Happ imports
    and excluded from Karing/fleet — surface is column-driven, not provider_kind-derived."""
    cdn = VlessServerConfig(
        server_label="CDN",
        country_code="NL",
        country_flag="🇳🇱",
        vless_link="vless://u@node67.safetunn.shop:443#bypass",
        surface="happ",
    )
    fleet = _server("Helsinki")  # surface='default'
    happ_selected = _select_servers_for_client((cdn, fleet), ua="Happ/2.0", client_param=None)
    kar_selected = _select_servers_for_client((cdn, fleet), ua="Karing/1.0", client_param=None)
    assert [s.server_label for s in happ_selected] == ["CDN"]
    assert [s.server_label for s in kar_selected] == ["Helsinki"]
