"""Sing-box (Happ) full-config renderer for /sub/.

When enabled (SUB_SINGBOX_HAPP_ENABLE=1) the subscription endpoint serves Happ
imports a complete sing-box JSON config instead of the base64 link list. The
config carries the same servers the link list would — parsed back out of their
``vless://`` URIs, so the link stays the single source of truth — PLUS the
client-side routing rule the link format cannot express: the Ozon app family
goes DIRECT from the subscriber's own ISP IP.

Server-side split routing already sends RU services direct from the entry, but
Ozon/QRator blocks hosting ASNs outright (Delta AS213220 confirmed on both its
old and fresh IPs, 2026-08-23), so for Ozon the only reliable exit is the
user's residential IP — routed around the tunnel on the client itself.

Transport mapping (vless URI -> sing-box outbound):
  tcp   + reality -> raw tcp, tls.reality, flow kept
  xhttp + reality -> splithttp (path/Host header)
  ws    + tls     -> ws + plain tls
  grpc            -> grpc with service_name (skipped when the link has none)

Links that do not map cleanly (e.g. the obfuscated CDN-bypass link_template
whose ``extra`` blob is Xray-only) are skipped rather than guessed at; if
nothing parses the caller falls back to the plain base64 list, so this can
never serve a config with zero working servers.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, unquote, urlsplit

from app.issuance.vless_provider import VlessServerConfig

_LOGGER = logging.getLogger(__name__)

# Ozon app family: storefront/API zones + the media CDN. Client-side only — the
# server-side ru_services list (canonicalize_routing.py) keeps its own copy for
# entry-side direct routing; the two serve different legs and evolve separately.
_OZON_DOMAIN_SUFFIXES: tuple[str, ...] = ("ozon.ru", "ozone.ru", "ozonusercontent.com")
# Android package of the Ozon app — matches every domain/protocol it touches.
_OZON_PACKAGE_NAMES: tuple[str, ...] = ("ru.ozon.shop",)

_PROXY_TAG = "proxy"
_DIRECT_TAG = "direct"
_DNS_LOCAL_TAG = "dns-local"
_DNS_REMOTE_TAG = "dns-remote"


def _parse_vless_link(link: str) -> dict | None:
    """Parse one ``vless://`` URI into a sing-box vless outbound (without tag).

    Returns ``None`` for anything that does not map cleanly onto sing-box —
    unknown transport, missing reality key, no TLS where TLS is required. The
    caller skips such servers instead of emitting a broken outbound (sing-box
    fails the WHOLE config on one malformed outbound).
    """
    try:
        u = urlsplit(link.strip())
        host, port, user = u.hostname, u.port, u.username
        if u.scheme != "vless" or not user or not host or not port:
            return None
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
    except ValueError:
        return None

    network = (q.get("type") or "tcp").lower()
    security = (q.get("security") or "").lower()

    tls: dict | None = None
    if security == "reality":
        pbk = q.get("pbk")
        if not pbk:
            return None
        tls = {
            "enabled": True,
            "server_name": q.get("sni") or host,
            "utls": {"enabled": True, "fingerprint": q.get("fp") or "chrome"},
            "reality": {"enabled": True, "public_key": pbk, "short_id": q.get("sid") or ""},
        }
    elif security == "tls":
        tls = {
            "enabled": True,
            "server_name": q.get("sni") or host,
            "utls": {"enabled": True, "fingerprint": q.get("fp") or "chrome"},
        }
    else:
        # ''/none/anything exotic (e.g. the CDN-bypass template without security=)
        # cannot be represented faithfully — skip.
        return None

    transport: dict | None = None
    if network == "tcp":
        pass
    elif network in ("xhttp", "splithttp"):
        transport = {"type": "splithttp"}
        if q.get("path"):
            transport["path"] = unquote(q["path"])
        transport["headers"] = {"Host": q.get("host") or q.get("sni") or host}
        # The link's mode=packet-up is deliberately NOT carried over: sing-box
        # splithttp gained `mode` only in recent cores and an unknown key makes
        # the whole config fail strict parsing. Plain stream splithttp still
        # works — the fleet's xhttp inbounds run mode='auto'.
    elif network == "ws":
        transport = {"type": "ws"}
        if q.get("path"):
            transport["path"] = unquote(q["path"])
        if q.get("host"):
            transport["headers"] = {"Host": q["host"]}
    elif network == "grpc":
        service = q.get("serviceName") or q.get("authority") or ""
        if not service:
            return None
        transport = {"type": "grpc", "service_name": service}
    else:
        return None

    outbound: dict = {"type": "vless", "server": host, "server_port": port, "uuid": user}
    if q.get("flow"):
        outbound["flow"] = q["flow"]
    if tls is not None:
        outbound["tls"] = tls
    if transport is not None:
        outbound["transport"] = transport
    return outbound


def build_singbox_config(
    servers: tuple[VlessServerConfig, ...] | list[VlessServerConfig],
) -> dict | None:
    """Build a full sing-box client config from the selected server links.

    All parsed servers sit behind a ``selector`` (tag ``proxy``) so the Happ UI
    keeps the server picker; route ``final`` goes to that selector. Ozon goes
    ``direct`` (package-name rule for the Android app + domain-suffix rule for
    everywhere else), and its DNS resolves via the system resolver so the whole
    path stays native. Returns ``None`` when no link parses — caller then falls
    back to the base64 link list.
    """
    outbounds: list[dict] = []
    tags: list[str] = []
    skipped: list[str] = []
    for idx, s in enumerate(servers):
        outbound = _parse_vless_link(s.vless_link)
        if outbound is None:
            skipped.append(s.server_label)
            continue
        tag = s.server_label.strip() or f"server-{idx}"
        if tag in tags:
            tag = f"{tag} {idx}"
        outbound["tag"] = tag
        outbounds.append(outbound)
        tags.append(tag)
    if not outbounds:
        return None
    if skipped:
        _LOGGER.warning("singbox config: skipped unparseable servers: %s", ", ".join(skipped))

    selector = {"type": "selector", "tag": _PROXY_TAG, "outbounds": tags, "default": tags[0]}
    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": {
            "servers": [
                {"tag": _DNS_LOCAL_TAG, "address": "local", "detour": _DIRECT_TAG},
                {"tag": _DNS_REMOTE_TAG, "address": "https://1.1.1.1/dns-query", "detour": _PROXY_TAG},
            ],
            "rules": [{"domain_suffix": list(_OZON_DOMAIN_SUFFIXES), "server": _DNS_LOCAL_TAG}],
            "final": _DNS_REMOTE_TAG,
            "strategy": "ipv4_only",
        },
        "inbounds": [
            {
                "type": "tun",
                "tag": "tun-in",
                "address": ["172.19.0.1/30", "fdfe:dcba:9876::1/126"],
                "mtu": 9000,
                "auto_route": True,
                "strict_route": True,
                "stack": "mixed",
                # Legacy inbound sniff fields: deprecated in sing-box 1.12 but
                # still honoured (auto-migrated). Domain rules are dead without
                # sniffing; the package_name rule works regardless.
                "sniff": True,
                "sniff_timeout": "300ms",
            }
        ],
        "outbounds": [selector, *outbounds, {"type": "direct", "tag": _DIRECT_TAG}],
        "route": {
            "rules": [
                {"package_name": list(_OZON_PACKAGE_NAMES), "outbound": _DIRECT_TAG},
                {"domain_suffix": list(_OZON_DOMAIN_SUFFIXES), "outbound": _DIRECT_TAG},
            ],
            "final": _PROXY_TAG,
            "auto_detect_interface": True,
        },
    }
