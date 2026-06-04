"""Thin Starlette ingress for ADM-01: JSON → execute_adm01_endpoint → JSON (no domain logic)."""

from __future__ import annotations

import json
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.admin_support.adm01_endpoint import (
    Adm01EndpointResponse,
    Adm01InboundRequest,
    Adm01LookupHandlerLike,
    execute_adm01_endpoint,
)
from app.admin_support.contracts import InternalAdminPrincipalExtractor
from app.admin_support.internal_auth import verify_internal_admin_secret

ADM01_INTERNAL_LOOKUP_PATH = "/internal/admin/adm01/lookup"


def adm01_endpoint_response_to_jsonable(resp: Adm01EndpointResponse) -> dict[str, Any]:
    out: dict[str, Any] = {
        "outcome": resp.outcome,
        "correlation_id": resp.correlation_id,
        "summary": None,
    }
    if resp.summary is not None:
        s = resp.summary
        out["summary"] = {
            "telegram_identity_known": s.telegram_identity_known,
            "subscription_bucket": s.subscription_bucket,
            "access_readiness_bucket": s.access_readiness_bucket,
            "recommended_next_action": s.recommended_next_action,
            "redaction": s.redaction,
        }
    return out


def _body_to_inbound(data: dict[str, Any]) -> Adm01InboundRequest:
    cid = data.get("correlation_id")
    correlation_id = cid if isinstance(cid, str) else ""
    pr = data.get("internal_admin_principal_id")
    principal = pr if isinstance(pr, str) else ""
    return Adm01InboundRequest(
        correlation_id=correlation_id,
        internal_admin_principal_id=principal,
        internal_user_id=data.get("internal_user_id"),
        telegram_user_id=data.get("telegram_user_id"),
    )


def create_adm01_internal_http_app(
    handler: Adm01LookupHandlerLike,
    principal_extractor: InternalAdminPrincipalExtractor,
) -> Starlette:
    async def adm01_lookup(request: Request) -> JSONResponse:
        auth_err = verify_internal_admin_secret(request)
        if auth_err is not None:
            return auth_err
        raw = await request.body()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "invalid_body"}, status_code=400)

        inbound = _body_to_inbound(data)
        resp = await execute_adm01_endpoint(handler, principal_extractor, inbound)
        return JSONResponse(adm01_endpoint_response_to_jsonable(resp), status_code=200)

    return Starlette(
        routes=[
            Route(ADM01_INTERNAL_LOOKUP_PATH, adm01_lookup, methods=["POST"]),
        ],
    )
