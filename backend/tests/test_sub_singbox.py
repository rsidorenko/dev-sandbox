"""Tests for the /sub/ sing-box (Happ) config renderer: vless:// URI parsing into
sing-box outbounds and the full-config assembly (selector + Ozon→direct rules)."""

from __future__ import annotations

from app.issuance.vless_provider import VlessServerConfig
from app.web_api.subscription_singbox import _parse_vless_link, build_singbox_config

_UUID = "bf10b762-1111-2222-3333-444455556666"

_TCP_LINK = (
    f"vless://{_UUID}@45.91.52.204:4443"
    "?type=tcp&security=reality&pbk=PBK123&fp=chrome&sni=yar.example&sid=ab12&spx=%2F"
    "&flow=xtls-rprx-vision#%F0%9F%87%B1%F0%9F%87%B9%20%D0%9B%D0%B8%D1%82%D0%B2%D0%B0%201.0"
)
_XHTTP_LINK = (
    f"vless://{_UUID}@45.91.52.204:9443"
    "?type=xhttp&security=reality&path=%2Fbravada-xhttp&pbk=PBK123&fp=chrome"
    "&sni=yar.example&sid=ab12&spx=%2F&mode=packet-up&extra=%7B%22mode%22%3A%22packet-up%22%7D"
    "#%F0%9F%87%B1%F0%9F%87%B9%20%D0%9B%D0%B8%D1%82%D0%B2%D0%B0%202.0"
)
_WS_LINK = (
    f"vless://{_UUID}@old.example.com:443"
    "?type=ws&security=tls&path=%2Fws&host=old.example.com&fp=chrome&sni=old.example.com#old"
)
# CDN-bypass template shape: type=xhttp but no security=/pbk → cannot map, must be skipped
_CDN_OPAQUE_LINK = f"vless://{_UUID}@cdn.strimingset.ru:443?extra=OBF&type=xhttp#bypass"


def _server(label: str, link: str) -> VlessServerConfig:
    return VlessServerConfig(server_label=label, country_code="LT", country_flag="🇱🇹", vless_link=link)


# ---------------------------------------------------------------------------
# _parse_vless_link — URI → sing-box outbound
# ---------------------------------------------------------------------------


def test_parse_tcp_reality_link() -> None:
    ob = _parse_vless_link(_TCP_LINK)
    assert ob is not None
    assert ob["type"] == "vless"
    assert ob["server"] == "45.91.52.204"
    assert ob["server_port"] == 4443
    assert ob["uuid"] == _UUID
    assert ob["flow"] == "xtls-rprx-vision"
    assert "transport" not in ob  # raw tcp
    tls = ob["tls"]
    assert tls["reality"] == {"enabled": True, "public_key": "PBK123", "short_id": "ab12"}
    assert tls["server_name"] == "yar.example"
    assert tls["utls"]["fingerprint"] == "chrome"


def test_parse_xhttp_reality_link() -> None:
    ob = _parse_vless_link(_XHTTP_LINK)
    assert ob is not None
    tr = ob["transport"]
    assert tr["type"] == "splithttp"
    assert tr["path"] == "/bravada-xhttp"
    assert tr["headers"]["Host"] == "yar.example"
    # mode/extra must NOT leak into the outbound: unknown splithttp keys fail
    # the whole config in strict sing-box parsing
    assert "mode" not in tr
    assert "extra" not in tr
    assert ob["tls"]["reality"]["public_key"] == "PBK123"
    assert "flow" not in ob


def test_parse_ws_tls_link() -> None:
    ob = _parse_vless_link(_WS_LINK)
    assert ob is not None
    assert ob["transport"] == {"type": "ws", "path": "/ws", "headers": {"Host": "old.example.com"}}
    assert ob["tls"]["server_name"] == "old.example.com"
    assert "reality" not in ob["tls"]


def test_parse_cdn_opaque_template_skipped() -> None:
    """The CDN-bypass link (no security=, obfuscated extra) cannot be represented —
    skipped rather than emitting a plaintext-ish broken outbound."""
    assert _parse_vless_link(_CDN_OPAQUE_LINK) is None


def test_parse_grpc_without_service_name_skipped() -> None:
    link = f"vless://{_UUID}@h.example:443?type=grpc&security=reality&pbk=P&sni=s#g"
    assert _parse_vless_link(link) is None


def test_parse_garbage_returns_none() -> None:
    for bad in ("", "not a link", "https://example.com/", f"vless://{_UUID}@host:notaport"):
        assert _parse_vless_link(bad) is None


def test_parse_reality_without_pbk_skipped() -> None:
    link = f"vless://{_UUID}@h.example:443?type=tcp&security=reality&sni=s#x"
    assert _parse_vless_link(link) is None


# ---------------------------------------------------------------------------
# build_singbox_config — full config assembly
# ---------------------------------------------------------------------------


def test_build_config_structure_selector_rules_dns() -> None:
    servers = [_server("Литва 1.0", _TCP_LINK), _server("Литва 2.0", _XHTTP_LINK)]
    cfg = build_singbox_config(servers)
    assert cfg is not None

    outbounds = cfg["outbounds"]
    selector = outbounds[0]
    assert selector["type"] == "selector" and selector["tag"] == "proxy"
    assert selector["outbounds"] == ["Литва 1.0", "Литва 2.0"]
    assert selector["default"] == "Литва 1.0"
    assert outbounds[-1] == {"type": "direct", "tag": "direct"}
    tags = [ob["tag"] for ob in outbounds]
    assert len(tags) == len(set(tags))  # unique

    rules = cfg["route"]["rules"]
    assert {"package_name": ["ru.ozon.shop"], "outbound": "direct"} in rules
    assert {"domain_suffix": ["ozon.ru", "ozone.ru", "ozonusercontent.com"], "outbound": "direct"} in rules
    assert cfg["route"]["final"] == "proxy"

    dns_rules = cfg["dns"]["rules"]
    assert dns_rules[0]["server"] == "dns-local"
    assert "ozon.ru" in dns_rules[0]["domain_suffix"]
    assert cfg["dns"]["final"] == "dns-remote"


def test_build_config_skips_unparseable_keeps_rest() -> None:
    servers = [_server("CDN обход", _CDN_OPAQUE_LINK), _server("Литва 2.0", _XHTTP_LINK)]
    cfg = build_singbox_config(servers)
    assert cfg is not None
    tags = [ob["tag"] for ob in cfg["outbounds"]]
    assert "Литва 2.0" in tags
    assert "CDN обход" not in tags


def test_build_config_none_when_nothing_parses() -> None:
    """All-unparseable set → None → endpoint falls back to the base64 link list."""
    assert build_singbox_config([_server("CDN", _CDN_OPAQUE_LINK)]) is None
    assert build_singbox_config([]) is None


def test_build_config_dedupes_duplicate_labels() -> None:
    servers = [_server("Дубль", _TCP_LINK), _server("Дубль", _XHTTP_LINK)]
    cfg = build_singbox_config(servers)
    assert cfg is not None
    tags = [ob["tag"] for ob in cfg["outbounds"]]
    assert len(tags) == len(set(tags))
    assert set(cfg["outbounds"][0]["outbounds"]) == set(tags[1:-1])
