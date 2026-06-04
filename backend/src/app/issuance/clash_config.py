"""Clash Meta YAML config builder for subscription endpoint.

Generates a Clash Meta configuration with routing rules that bypass VPN
for Russian domains (.ru, .su, .рф), sending them directly via the
user's local ISP instead of through the VLESS proxy.
"""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from app.issuance.singbox_config import _UNSUPPORTED_TRANSPORTS, _parse_vless_link
from app.issuance.vless_provider import VlessServerConfig


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
        if p["flow"]:
            proxy["flow"] = p["flow"]
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
        if "flow" in p:
            lines.append(f"    flow: {p['flow']}")
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

    # Rules
    lines.append("")
    lines.append("rules:")
    for suffix in (".ru", ".su", ".рф"):
        lines.append(f"  - DOMAIN-SUFFIX,{suffix},DIRECT")
    lines.append("  - MATCH,proxy")

    return "\n".join(lines) + "\n"
