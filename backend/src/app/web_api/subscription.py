"""Public subscription endpoint: /sub/{token} → base64-encoded VLESS links."""

from __future__ import annotations

import base64

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.issuance.vless_provider import VlessProviderOutcome


async def handle_subscription(request: Request) -> PlainTextResponse:
    pool = request.app.state.pool
    token = request.path_params["token"]

    row = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE subscription_token = $1",
        token,
    )
    if row is None:
        return PlainTextResponse("not found", status_code=404)

    from app.issuance.xui_vless_provider import XuiVlessProvider

    provider = XuiVlessProvider(pool)
    result = await provider.get_user_config(internal_user_id=row["internal_user_id"])
    if result.outcome != VlessProviderOutcome.SUCCESS or result.config is None:
        return PlainTextResponse("unavailable", status_code=503)

    links = "\n".join(s.vless_link for s in result.config.servers)
    encoded = base64.b64encode(links.encode("utf-8")).decode("utf-8")
    return PlainTextResponse(encoded, media_type="text/plain")
