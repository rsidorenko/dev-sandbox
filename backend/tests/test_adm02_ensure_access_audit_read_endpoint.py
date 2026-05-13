"""Tests for ADM-02 ensure-access audit read endpoint adapter and handler."""

from __future__ import annotations

import asyncio
import json

from app.admin_support.adm02_ensure_access_audit_read import Adm02EnsureAccessAuditLookupHandler
from app.admin_support.adm02_ensure_access_audit_read_endpoint import (
    Adm02EnsureAccessAuditLookupInboundRequest,
    execute_adm02_ensure_access_audit_lookup_endpoint,
)
from app.admin_support.contracts import (
    Adm01SupportAccessReadinessBucket,
    Adm02EnsureAccessAuditEventType,
    Adm02EnsureAccessAuditEvidenceItem,
    Adm02EnsureAccessAuditOutcomeBucket,
    Adm02EnsureAccessAuditPrincipalMarker,
    Adm02EnsureAccessAuditReadResult,
    Adm02EnsureAccessRemediationResult,
    AdminActorRef,
    InternalAdminPrincipalExtractionInput,
    InternalAdminPrincipalExtractionOutcome,
    InternalAdminPrincipalExtractionResult,
)
from app.shared.correlation import new_correlation_id

_FORBIDDEN = (
    "database_url",
    "postgres://",
    "postgresql://",
    "bearer ",
    "private key",
    "begin ",
    "token=",
    "vpn://",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "schema_version",
    "customer_ref",
    "provider_ref",
    "checkout_attempt_id",
    "internal_user_id",
)


def _run(coro):
    return asyncio.run(coro)


class _Extractor:
    async def extract_trusted_internal_admin_principal(self, inp: InternalAdminPrincipalExtractionInput):
        _ = inp
        return InternalAdminPrincipalExtractionResult(
            outcome=InternalAdminPrincipalExtractionOutcome.SUCCESS,
            principal=AdminActorRef(internal_admin_principal_id="adm-x"),
        )


class _Authorization:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed

    async def check_adm02_ensure_access_allowed(self, actor: AdminActorRef, *, correlation_id: str) -> bool:
        _ = (actor, correlation_id)
        return self._allowed


class _ReadPort:
    async def read_ensure_access_audit_evidence(self, query):
        _ = query
        return Adm02EnsureAccessAuditReadResult(
            items=(
                Adm02EnsureAccessAuditEvidenceItem(
                    created_at="2026-04-26T00:00:00+00:00",
                    event_type=Adm02EnsureAccessAuditEventType.ENSURE_ACCESS,
                    outcome_bucket=Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS,
                    remediation_result=Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
                    readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
                    principal_marker=Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED,
                    correlation_id="a" * 32,
                    source_marker="internal_admin_runtime",
                ),
            )
        )


def test_endpoint_success_by_correlation_returns_safe_item_list() -> None:
    cid = new_correlation_id()
    handler = Adm02EnsureAccessAuditLookupHandler(
        authorization=_Authorization(True),
        audit_read=_ReadPort(),
    )
    response = _run(
        execute_adm02_ensure_access_audit_lookup_endpoint(
            handler,
            _Extractor(),
            Adm02EnsureAccessAuditLookupInboundRequest(
                correlation_id=cid,
                internal_admin_principal_id="adm-x",
                evidence_correlation_id="a" * 32,
                limit=10,
            ),
        )
    )
    assert response.outcome == "success"
    assert len(response.items) == 1
    payload = json.dumps(
        {
            "outcome": response.outcome,
            "correlation_id": response.correlation_id,
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
                for item in response.items
            ],
        },
        sort_keys=True,
    ).lower()
    for forbidden in _FORBIDDEN:
        assert forbidden not in payload


def test_endpoint_unauthorized_is_denied_without_items() -> None:
    cid = new_correlation_id()
    handler = Adm02EnsureAccessAuditLookupHandler(
        authorization=_Authorization(False),
        audit_read=_ReadPort(),
    )
    response = _run(
        execute_adm02_ensure_access_audit_lookup_endpoint(
            handler,
            _Extractor(),
            Adm02EnsureAccessAuditLookupInboundRequest(
                correlation_id=cid,
                internal_admin_principal_id="adm-x",
                evidence_correlation_id=None,
                limit=20,
            ),
        )
    )
    assert response.outcome == "denied"
    assert response.items == ()


def test_endpoint_invalid_limit_returns_safe_invalid_input() -> None:
    cid = new_correlation_id()
    handler = Adm02EnsureAccessAuditLookupHandler(
        authorization=_Authorization(True),
        audit_read=_ReadPort(),
    )
    response = _run(
        execute_adm02_ensure_access_audit_lookup_endpoint(
            handler,
            _Extractor(),
            Adm02EnsureAccessAuditLookupInboundRequest(
                correlation_id=cid,
                internal_admin_principal_id="adm-x",
                evidence_correlation_id=None,
                limit=1000,
            ),
        )
    )
    assert response.outcome == "invalid_input"
    assert response.items == ()
