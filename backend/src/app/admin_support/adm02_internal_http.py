"""Thin Starlette ingress for ADM-02: JSON → execute_adm02_endpoint → JSON (no domain logic)."""

from __future__ import annotations

import json
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.admin_support.adm02_endpoint import (
    Adm02DiagnosticsHandlerLike,
    Adm02EndpointResponse,
    Adm02InboundRequest,
    execute_adm02_endpoint,
)
from app.admin_support.adm02_ensure_access_audit_read_endpoint import (
    DEFAULT_AUDIT_EVIDENCE_LIMIT,
    Adm02EnsureAccessAuditLookupEndpointResponse,
    Adm02EnsureAccessAuditLookupHandlerLike,
    Adm02EnsureAccessAuditLookupInboundRequest,
    execute_adm02_ensure_access_audit_lookup_endpoint,
)
from app.admin_support.adm02_ensure_access_endpoint import (
    Adm02EnsureAccessEndpointResponse,
    Adm02EnsureAccessHandlerLike,
    Adm02EnsureAccessInboundRequest,
    execute_adm02_ensure_access_endpoint,
)
from app.admin_support.contracts import InternalAdminPrincipalExtractor

ADM02_INTERNAL_DIAGNOSTICS_PATH = "/internal/admin/adm02/diagnostics"
ADM02_INTERNAL_ENSURE_ACCESS_PATH = "/internal/admin/adm02/ensure-access"
ADM02_INTERNAL_AUDIT_EVENTS_PATH = "/internal/admin/adm02/audit-events"


def adm02_endpoint_response_to_jsonable(resp: Adm02EndpointResponse) -> dict[str, Any]:
    out: dict[str, Any] = {
        "outcome": resp.outcome,
        "correlation_id": resp.correlation_id,
        "summary": None,
    }
    if resp.summary is not None:
        s = resp.summary
        out["summary"] = {
            "billing_category": s.billing_category,
            "internal_fact_refs": list(s.internal_fact_refs),
            "quarantine_marker": s.quarantine_marker,
            "quarantine_reason_code": s.quarantine_reason_code,
            "reconciliation_last_run_marker": s.reconciliation_last_run_marker,
            "redaction": s.redaction,
        }
    return out


def _body_to_inbound(data: dict[str, Any]) -> Adm02InboundRequest:
    cid = data.get("correlation_id")
    correlation_id = cid if isinstance(cid, str) else ""
    pr = data.get("internal_admin_principal_id")
    principal = pr if isinstance(pr, str) else ""
    return Adm02InboundRequest(
        correlation_id=correlation_id,
        internal_admin_principal_id=principal,
        internal_user_id=data.get("internal_user_id"),
        telegram_user_id=data.get("telegram_user_id"),
    )


def adm02_ensure_access_endpoint_response_to_jsonable(
    resp: Adm02EnsureAccessEndpointResponse,
) -> dict[str, Any]:
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
            "remediation_result": s.remediation_result,
            "recommended_next_action": s.recommended_next_action,
        }
    return out


def _body_to_ensure_access_inbound(data: dict[str, Any]) -> Adm02EnsureAccessInboundRequest:
    cid = data.get("correlation_id")
    correlation_id = cid if isinstance(cid, str) else ""
    pr = data.get("internal_admin_principal_id")
    principal = pr if isinstance(pr, str) else ""
    return Adm02EnsureAccessInboundRequest(
        correlation_id=correlation_id,
        internal_admin_principal_id=principal,
        internal_user_id=data.get("internal_user_id"),
        telegram_user_id=data.get("telegram_user_id"),
    )


def adm02_ensure_access_audit_lookup_response_to_jsonable(
    resp: Adm02EnsureAccessAuditLookupEndpointResponse,
) -> dict[str, Any]:
    return {
        "outcome": resp.outcome,
        "correlation_id": resp.correlation_id,
        "items": [
            {
                "created_at": item.created_at,
                "event_type": item.event_type,
                "outcome_bucket": item.outcome_bucket,
                "remediation_result": item.remediation_result,
                "readiness_bucket": item.readiness_bucket,
                "principal_marker": item.principal_marker,
                "correlation_id": item.correlation_id,
                "source_marker": item.source_marker,
            }
            for item in resp.items
        ],
    }


def _body_to_audit_lookup_inbound(data: dict[str, Any]) -> Adm02EnsureAccessAuditLookupInboundRequest:
    cid = data.get("correlation_id")
    correlation_id = cid if isinstance(cid, str) else ""
    pr = data.get("internal_admin_principal_id")
    principal = pr if isinstance(pr, str) else ""
    evidence_cid = data.get("evidence_correlation_id")
    safe_evidence_cid = evidence_cid if isinstance(evidence_cid, str) else None
    limit_raw = data.get("limit")
    limit = limit_raw if type(limit_raw) is int else DEFAULT_AUDIT_EVIDENCE_LIMIT
    return Adm02EnsureAccessAuditLookupInboundRequest(
        correlation_id=correlation_id,
        internal_admin_principal_id=principal,
        evidence_correlation_id=safe_evidence_cid,
        limit=limit,
    )


def create_adm02_internal_http_app(
    handler: Adm02DiagnosticsHandlerLike,
    principal_extractor: InternalAdminPrincipalExtractor,
    ensure_access_handler: Adm02EnsureAccessHandlerLike | None = None,
    ensure_access_audit_lookup_handler: Adm02EnsureAccessAuditLookupHandlerLike | None = None,
) -> Starlette:
    async def adm02_diagnostics(request: Request) -> JSONResponse:
        raw = await request.body()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "invalid_body"}, status_code=400)

        inbound = _body_to_inbound(data)
        resp = await execute_adm02_endpoint(handler, principal_extractor, inbound)
        return JSONResponse(adm02_endpoint_response_to_jsonable(resp), status_code=200)

    async def adm02_ensure_access(request: Request) -> JSONResponse:
        if ensure_access_handler is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        raw = await request.body()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "invalid_body"}, status_code=400)
        inbound = _body_to_ensure_access_inbound(data)
        resp = await execute_adm02_ensure_access_endpoint(
            ensure_access_handler,
            principal_extractor,
            inbound,
        )
        return JSONResponse(adm02_ensure_access_endpoint_response_to_jsonable(resp), status_code=200)

    async def adm02_audit_events(request: Request) -> JSONResponse:
        if ensure_access_audit_lookup_handler is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        raw = await request.body()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "invalid_body"}, status_code=400)
        inbound = _body_to_audit_lookup_inbound(data)
        resp = await execute_adm02_ensure_access_audit_lookup_endpoint(
            ensure_access_audit_lookup_handler,
            principal_extractor,
            inbound,
        )
        return JSONResponse(adm02_ensure_access_audit_lookup_response_to_jsonable(resp), status_code=200)

    routes: list[Route] = [
        Route(ADM02_INTERNAL_DIAGNOSTICS_PATH, adm02_diagnostics, methods=["POST"]),
    ]
    if ensure_access_handler is not None:
        routes.append(Route(ADM02_INTERNAL_ENSURE_ACCESS_PATH, adm02_ensure_access, methods=["POST"]))
    if ensure_access_audit_lookup_handler is not None:
        routes.append(Route(ADM02_INTERNAL_AUDIT_EVENTS_PATH, adm02_audit_events, methods=["POST"]))

    return Starlette(
        routes=routes,
    )
