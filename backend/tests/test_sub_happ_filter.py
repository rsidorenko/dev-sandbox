"""Tests for the Happ/Karing subscription split: UA-aware server selection in /sub/
and the provider's bare_xray (Cloud.ru) link builder, plus the /sub/ entitlement gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.issuance.vless_provider import VlessServerConfig
from app.issuance.xui_client import XuiServerConfig
from app.issuance.xui_vless_provider import (
    XuiVlessProvider,
    _build_vless_link,
    _vless_uuid_for_transport,
)
from app.web_api.subscription import _entitlement_active, _select_servers_for_client

_NOW = datetime(2026, 8, 3, 12, tzinfo=UTC)
_FUTURE = _NOW + timedelta(days=10)
_PAST = _NOW - timedelta(days=1)


def _row(**overrides) -> dict:
    base = {
        "state_label": "active",
        "plan_id": None,
        "trial_expires_at": None,
        "active_until_utc": None,
        "keys_deleted_at": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _entitlement_active — /sub/ serves keys only to currently-valid subscriptions
# ---------------------------------------------------------------------------


def test_entitlement_active_trial_valid() -> None:
    assert _entitlement_active(_row(plan_id=None, trial_expires_at=_FUTURE), now=_NOW) is True


def test_entitlement_active_paid_valid() -> None:
    assert _entitlement_active(_row(plan_id="1m", active_until_utc=_FUTURE), now=_NOW) is True


def test_entitlement_expired_trial_blocked() -> None:
    assert _entitlement_active(_row(plan_id=None, trial_expires_at=_PAST), now=_NOW) is False


def test_entitlement_expired_paid_blocked() -> None:
    assert _entitlement_active(_row(plan_id="1m", active_until_utc=_PAST), now=_NOW) is False


def test_entitlement_expired_state_blocked_even_with_future_ts() -> None:
    """state_label='expired' wins — a flipped state blocks /sub/ even if a timestamp
    is oddly in the future (defensive; reconcile/remove already happened on expiry)."""
    assert _entitlement_active(_row(state_label="expired", plan_id="1m", active_until_utc=_FUTURE), now=_NOW) is False


def test_entitlement_keys_deleted_blocked() -> None:
    assert _entitlement_active(_row(plan_id="1m", active_until_utc=_FUTURE, keys_deleted_at=_NOW), now=_NOW) is False


def test_entitlement_renewal_after_expiry_serves_keys_again() -> None:
    """Renewal flips the snapshot back to state='active' with a future active_until
    (fulfillment sets both in one transaction right after the UC-05 apply that would
    otherwise clobber them). A previously-expired user is entitled again on renewal —
    the gate re-checks the DB every request, no stale 403."""
    # before renewal: expired, blocked
    assert _entitlement_active(_row(state_label="expired", plan_id="1m", active_until_utc=_PAST), now=_NOW) is False
    # after renewal: active + future — keys served again
    assert _entitlement_active(_row(state_label="active", plan_id="1m", active_until_utc=_FUTURE), now=_NOW) is True


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


def test_both_surface_served_to_happ_and_default() -> None:
    """surface='both' entries appear in BOTH the Happ selection and the default/Karing
    selection — a Cloud.ru 1.0 relay promoted to universal when the direct fleet 1.0
    is DPI-dead for Karing."""
    fleet = _server("Frankfurt 2.0")  # surface='default'
    happ_only = _server("Cloud.ru 2.0", "happ")
    universal = _server("Cloud.ru 1.0", "both")
    servers = (fleet, happ_only, universal)

    happ_selected = _select_servers_for_client(servers, ua="Happ/2.0", client_param=None)
    assert {s.server_label for s in happ_selected} == {"Cloud.ru 2.0", "Cloud.ru 1.0"}

    kar_selected = _select_servers_for_client(servers, ua="Karing/1.0", client_param=None)
    assert {s.server_label for s in kar_selected} == {"Frankfurt 2.0", "Cloud.ru 1.0"}


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
        server_id=34,
        label="Обход CDN",
        country_code="",
        country_flag="\U0001f310",
        server_host="cdn.strimingset.ru",
        server_port=443,
        ws_path="api-test",
        tls_sni="cdn.strimingset.ru",
        panel_url="",
        panel_username="",
        panel_password="",
        inbound_id=0,
        transport_type="xhttp",
        link_template="vless://{UUID}@cdn.strimingset.ru:443?extra=OBF&type=xhttp#bypass",
        provider_kind="bare_xray",
        client_surface="happ",
    )


def test_cdn_bypass_link_template_emitted_verbatim_per_user() -> None:
    """The CDN whitelist-bypass row (link_template, client_surface='happ') emits the full
    obfuscated CDN vless:// verbatim with {UUID} -> per-user uuid (the `extra` obfuscation
    is fixed). It's an Xray-core/Happ-only key, so its surface is 'happ'."""
    link = _build_vless_link(_cdn_config(), "user-uuid-123")
    assert link == "vless://user-uuid-123@cdn.strimingset.ru:443?extra=OBF&type=xhttp#bypass"
    assert "extra=OBF" in link  # obfuscation preserved verbatim


def test_cdn_bypass_surface_routes_to_happ_only() -> None:
    """A row with client_surface='happ' (the CDN bypass) is served to Happ imports
    and excluded from Karing/fleet — surface is column-driven, not provider_kind-derived."""
    cdn = VlessServerConfig(
        server_label="CDN",
        country_code="",
        country_flag="\U0001f310",
        vless_link="vless://u@cdn.strimingset.ru:443#bypass",
        surface="happ",
    )
    fleet = _server("Helsinki")  # surface='default'
    happ_selected = _select_servers_for_client((cdn, fleet), ua="Happ/2.0", client_param=None)
    kar_selected = _select_servers_for_client((cdn, fleet), ua="Karing/1.0", client_param=None)
    assert [s.server_label for s in happ_selected] == ["CDN"]
    assert [s.server_label for s in kar_selected] == ["Helsinki"]
