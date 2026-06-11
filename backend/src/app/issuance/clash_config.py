"""Clash Meta YAML config builder for subscription endpoint.

Generates a Clash Meta configuration that routes all traffic through the
VLESS proxy. Split routing (Russian domains via Russian server, everything
else via foreign servers) is handled at the VPN server level, not client-side.
"""

from __future__ import annotations

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


def _vless_link_to_clash_proxy(link: str) -> dict:
    """Convert a vless:// URI to a Clash Meta proxy dict."""
    p = _parse_vless_link(link)
    proxy: dict = {
        "name": p["label"],
        "type": "vless",
        "server": p["host"],
        "port": p["port"],
        "uuid": p["uuid"],
        "udp": True,
        "tls": True,
        "client-fingerprint": p["fp"] or "chrome",
    }

    if p["security"] == "reality":
        proxy["servername"] = p["sni"]
        proxy["reality-opts"] = {
            "public-key": p["pbk"],
            "short-id": p["sid"],
        }
    elif p["security"] == "tls":
        sni = p["sni"] or p["host_header"] or p["host"]
        proxy["servername"] = sni

    if p["type"] == "ws":
        ws_opts: dict = {}
        if p["path"]:
            ws_opts["path"] = p["path"]
        if p["host_header"]:
            ws_opts["headers"] = {"Host": p["host_header"]}
        proxy["network"] = "ws"
        proxy["ws-opts"] = ws_opts
    else:
        proxy["network"] = "tcp"

    return proxy


def build_clash_config(servers: tuple[VlessServerConfig, ...]) -> str:
    """Build a Clash Meta YAML config with Russian domain bypass."""
    supported = tuple(
        s for s in servers
        if _parse_vless_link(s.vless_link)["type"] not in _UNSUPPORTED_TRANSPORTS
    )
    if not supported:
        return "proxies: []\nrules: []\n"

    proxies = [_vless_link_to_clash_proxy(s.vless_link) for s in supported]
    proxy_names = [p["name"] for p in proxies]

    lines: list[str] = []

    # Proxies
    lines.append("proxies:")
    for p in proxies:
        name = p["name"]
        lines.append(f'  - name: "{name}"')
        lines.append(f"    type: {p['type']}")
        lines.append(f"    server: {p['server']}")
        lines.append(f"    port: {p['port']}")
        lines.append(f"    uuid: {p['uuid']}")
        lines.append(f"    udp: true")
        lines.append(f"    tls: true")
        lines.append(f"    client-fingerprint: {p['client-fingerprint']}")

        if "servername" in p:
            lines.append(f"    servername: {p['servername']}")
        if "reality-opts" in p:
            lines.append("    reality-opts:")
            lines.append(f'      public-key: {p["reality-opts"]["public-key"]}')
            lines.append(f'      short-id: {p["reality-opts"]["short-id"]}')
        lines.append(f"    network: {p.get('network', 'tcp')}")
        if p.get("network") == "ws" and "ws-opts" in p:
            lines.append("    ws-opts:")
            if "path" in p["ws-opts"]:
                lines.append(f"      path: {p['ws-opts']['path']}")
            if "headers" in p["ws-opts"]:
                lines.append("      headers:")
                for k, v in p["ws-opts"]["headers"].items():
                    lines.append(f"        {k}: {v}")

    # Proxy groups
    lines.append("")
    lines.append("proxy-groups:")
    lines.append("  - name: proxy")
    lines.append("    type: select")
    lines.append("    proxies:")
    for name in proxy_names:
        lines.append(f'      - "{name}"')

    # Rules — all traffic through proxy; server handles split routing
    lines.append("")
    lines.append("rules:")
    lines.append("  - MATCH,proxy")

    return "\n".join(lines) + "\n"
