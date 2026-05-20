"""Public subscription endpoint: /sub/{token} → base64-encoded VLESS links."""

from __future__ import annotations

import base64
import time

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from app.issuance.vless_provider import VlessProviderOutcome

_SUB_RATE_LIMIT_MAX = 30
_SUB_RATE_LIMIT_WINDOW = 60
_sub_rate_limit_store: dict[str, list[float]] = {}


def _check_sub_rate_limit(client_ip: str) -> bool:
    now = time.monotonic()
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

    row = await pool.fetchrow(
        "SELECT internal_user_id, subscription_token_expires_at FROM user_identities WHERE subscription_token = $1",
        token,
    )
    if row is None:
        return PlainTextResponse("not found", status_code=404)

    # Check token expiry — if expired, reject (new token will be issued on next get_user_config)
    from datetime import UTC, datetime

    expires_at = row["subscription_token_expires_at"]
    if expires_at is not None and expires_at <= datetime.now(UTC):
        return PlainTextResponse("token expired", status_code=410)

    from app.issuance.xui_vless_provider import XuiVlessProvider

    provider = XuiVlessProvider(pool)
    result = await provider.get_user_config(internal_user_id=row["internal_user_id"])
    if result.outcome != VlessProviderOutcome.SUCCESS or result.config is None:
        return PlainTextResponse("unavailable", status_code=503)

    links = "\n".join(s.vless_link for s in result.config.servers)
    encoded = base64.b64encode(links.encode("utf-8")).decode("utf-8")
    return PlainTextResponse(encoded, media_type="text/plain")
