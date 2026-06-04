"""SING-BOX JSON config builder for subscription endpoint.

Generates a SING-BOX configuration that routes all traffic through the
VLESS proxy. Split routing (Russian domains via Russian server, everything
else via foreign servers) is handled at the VPN server level, not client-side.
"""

from __future__ import annotations

import json
from urllib.parse import unquote, urlparse

from app.issuance.vless_provider import VlessServerConfig

_UNSUPPORTED_TRANSPORTS = frozenset({"xhttp"})


def _parse_vless_link(link: str) -> dict:
    """Parse a vless:// URI into connection parameters."""
    parsed = urlparse(link)
    uuid = parsed.username or ""
    host = parsed.hostname or ""
    port = parsed.port or 443
    fragment = unquote(parsed.fragment)

    params: dict[str, str] = {}
    for pair in parsed.query.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = unquote(v)

    return {
        "uuid": uuid,
        "host": host,
        "port": port,
        "label": fragment,
        "type": params.get("type", "tcp"),
        "security": params.get("security", ""),
        "pbk": params.get("pbk", ""),
        "sid": params.get("sid", ""),
        "sni": params.get("sni", ""),
        "fp": params.get("fp", "chrome"),
        "flow": params.get("flow", ""),
        "path": params.get("path", ""),
        "host_header": params.get("host", ""),
    }


def _vless_link_to_outbound(link: str) -> dict:
    """Convert a vless:// URI to a SING-BOX outbound dict."""
    p = _parse_vless_link(link)
    outbound: dict = {
        "type": "vless",
        "tag": p["label"],
        "server": p["host"],
        "server_port": p["port"],
        "uuid": p["uuid"],
    }

    # TLS configuration
    tls: dict = {"enabled": True}
    if p["security"] == "reality":
        tls["server_name"] = p["sni"]
        tls["reality"] = {
            "enabled": True,
            "public_key": p["pbk"],
            "short_id": p["sid"],
        }
        tls["utls"] = {"enabled": True, "fingerprint": p["fp"]}
    elif p["security"] == "tls":
        sni = p["sni"] or p["host_header"] or p["host"]
        tls["server_name"] = sni
        tls["utls"] = {"enabled": True, "fingerprint": p["fp"]}

    outbound["tls"] = tls

    # Transport
    if p["type"] == "ws":
        transport: dict = {"type": "ws"}
        if p["path"]:
            transport["path"] = p["path"]
        if p["host_header"]:
            transport["headers"] = {"Host": p["host_header"]}
        outbound["transport"] = transport
    elif p["type"] == "xhttp":
        transport = {"type": "splithttp"}
        if p["path"]:
            transport["path"] = p["path"]
        outbound["transport"] = transport

    # Flow (TCP+Reality uses xtls-rprx-vision)
    if p["flow"]:
        outbound["flow"] = p["flow"]

    return outbound


def build_singbox_config(servers: tuple[VlessServerConfig, ...]) -> str:
    """Build a complete SING-BOX JSON config with Russian domain bypass."""
    if not servers:
        return json.dumps({"outbounds": [], "route": {}})

    supported = tuple(
        s for s in servers
        if _parse_vless_link(s.vless_link)["type"] not in _UNSUPPORTED_TRANSPORTS
    )
    if not supported:
        return json.dumps({"outbounds": [], "route": {}})

    proxy_outbounds = [_vless_link_to_outbound(s.vless_link) for s in supported]
    server_tags = [ob["tag"] for ob in proxy_outbounds]

    selector: dict = {
        "type": "selector",
        "tag": "proxy",
        "outbounds": server_tags,
        "default": server_tags[0],
    }

    # Utility outbounds
    direct: dict = {"type": "direct", "tag": "direct"}
    block: dict = {"type": "block", "tag": "block"}
    dns_out: dict = {"type": "dns", "tag": "dns-out"}

    config = {
        "outbounds": [selector, *proxy_outbounds, direct, block, dns_out],
        "route": {
            "rules": [
                {"protocol": "dns", "outbound": "dns-out"},
            ],
            "final": "proxy",
            "auto_detect_interface": True,
        },
    }

    return json.dumps(config, ensure_ascii=False, indent=2)
