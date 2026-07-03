"""Public subscription endpoint: /sub/{token} → base64-encoded VLESS links."""

from __future__ import annotations

import base64
import time
from datetime import UTC, datetime
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, Response

from app.issuance.vless_provider import VlessProviderOutcome, VlessServerConfig

_SUB_RATE_LIMIT_MAX = 30
_SUB_RATE_LIMIT_WINDOW = 60
_SUB_RATE_LIMIT_MAX_ENTRIES = 10000
_sub_rate_limit_store: dict[str, list[float]] = {}
_sub_last_cleanup: float = 0.0


def _check_sub_rate_limit(client_ip: str) -> bool:
    global _sub_last_cleanup
    now = time.monotonic()
    if now - _sub_last_cleanup > _SUB_RATE_LIMIT_WINDOW:
        expired = [k for k, v in _sub_rate_limit_store.items() if not v or now - v[-1] > _SUB_RATE_LIMIT_WINDOW]
        for k in expired:
            del _sub_rate_limit_store[k]
        if len(_sub_rate_limit_store) > _SUB_RATE_LIMIT_MAX_ENTRIES:
            oldest = sorted(
                _sub_rate_limit_store, key=lambda k: _sub_rate_limit_store[k][-1] if _sub_rate_limit_store[k] else 0
            )
            for k in oldest[: len(_sub_rate_limit_store) - _SUB_RATE_LIMIT_MAX_ENTRIES]:
                del _sub_rate_limit_store[k]
        _sub_last_cleanup = now
    window = _sub_rate_limit_store.get(client_ip, [])
    window = [t for t in window if now - t < _SUB_RATE_LIMIT_WINDOW]
    if len(window) >= _SUB_RATE_LIMIT_MAX:
        _sub_rate_limit_store[client_ip] = window
        return False
    window.append(now)
    _sub_rate_limit_store[client_ip] = window
    return True


async def handle_subscription(request: Request) -> PlainTextResponse | Response:
    client_ip = request.client.host if request.client else "unknown"
    if not _check_sub_rate_limit(client_ip):
        return Response("rate limited", status_code=429)

    pool = request.app.state.pool
    token = request.path_params["token"]

    # ?open=karing|v2raytun → redirect to app URL scheme for auto-import
    open_action = request.query_params.get("open")
    if open_action in ("karing", "v2raytun"):
        # Build https URL — behind nginx the scheme is http
        forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        sub_url = f"{forwarded_proto}://{request.url.netloc}{request.url.path}"
        if open_action == "karing":
            app_url = f"karing://install-config?url={quote(sub_url, safe='')}&name=Bravada%20VPN"
        else:
            app_url = f"v2raytun://import/{sub_url}"
        return HTMLResponse(
            f'<html><head><meta http-equiv="refresh" content="0;url={app_url}">'
            f"</head><body><p>Opening {open_action}...</p>"
            f'<p><a href="{app_url}">Tap here if nothing happens</a></p></body></html>'
        )

    row = await pool.fetchrow(
        "SELECT internal_user_id, subscription_token_expires_at FROM user_identities WHERE subscription_token = $1",
        token,
    )
    if row is None:
        return PlainTextResponse("not found", status_code=404)
    if row.get("subscription_token_expires_at") and row["subscription_token_expires_at"] < datetime.now(UTC):
        return PlainTextResponse("token expired", status_code=410)

    provider = request.app.state.vless_provider

    result = await provider.get_user_config(internal_user_id=row["internal_user_id"])
    if result.outcome != VlessProviderOutcome.SUCCESS or result.config is None:
        return PlainTextResponse("unavailable", status_code=503)

    servers = _select_servers_for_client(
        result.config.servers,
        ua=request.headers.get("user-agent", ""),
        client_param=request.query_params.get("client"),
    )

    links = "\n".join(s.vless_link for s in servers)
    encoded = base64.b64encode(links.encode("utf-8")).decode("utf-8")
    return PlainTextResponse(encoded, media_type="text/plain")


def _select_servers_for_client(
    servers: tuple[VlessServerConfig, ...] | list[VlessServerConfig],
    *,
    ua: str,
    client_param: str | None,
) -> list[VlessServerConfig]:
    """Pick which server links to serve based on the importing client.

    A Happ import (User-Agent contains 'happ', or ?client=happ override) gets only
    surface='happ' entries — the bare_xray RU-relay entry (Cloud.ru) that works in
    Happ; the foreign 3x-ui fleet does not. Every other client (Karing, v2rayN, …)
    gets surface='default' (the fleet), exactly as before. Falls back to the full set
    when the requested surface is empty (e.g. Cloud.ru row inactive).
    """
    want_happ = "happ" in (ua or "").lower() or client_param == "happ"
    if want_happ:
        happ = [s for s in servers if s.surface == "happ"]
        return happ if happ else list(servers)
    return [s for s in servers if s.surface == "default"]
