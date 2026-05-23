"""Public subscription endpoint: /sub/{token} → base64-encoded VLESS links."""

from __future__ import annotations

import base64
import time

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from app.issuance.vless_provider import VlessProviderOutcome

_SUB_RATE_LIMIT_MAX = 30
_SUB_RATE_LIMIT_WINDOW = 60
_SUB_RATE_LIMIT_MAX_ENTRIES = 10000
_sub_rate_limit_store: dict[str, list[float]] = {}
_sub_last_cleanup: float = 0.0


def _check_sub_rate_limit(client_ip: str) -> bool:
    global _sub_last_cleanup
    now = time.monotonic()
    # Periodic cleanup: remove old entries every 60s
    if now - _sub_last_cleanup > _SUB_RATE_LIMIT_WINDOW:
        expired = [k for k, v in _sub_rate_limit_store.items() if not v or now - v[-1] > _SUB_RATE_LIMIT_WINDOW]
        for k in expired:
            del _sub_rate_limit_store[k]
        # Cap total entries
        if len(_sub_rate_limit_store) > _SUB_RATE_LIMIT_MAX_ENTRIES:
            oldest = sorted(_sub_rate_limit_store, key=lambda k: _sub_rate_limit_store[k][-1] if _sub_rate_limit_store[k] else 0)
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

    row = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE subscription_token = $1",
        token,
    )
    if row is None:
        return PlainTextResponse("not found", status_code=404)

    provider = request.app.state.vless_provider

    result = await provider.get_user_config(internal_user_id=row["internal_user_id"])
    if result.outcome != VlessProviderOutcome.SUCCESS or result.config is None:
        return PlainTextResponse("unavailable", status_code=503)

    links = "\n".join(s.vless_link for s in result.config.servers)
    encoded = base64.b64encode(links.encode("utf-8")).decode("utf-8")
    return PlainTextResponse(encoded, media_type="text/plain")
