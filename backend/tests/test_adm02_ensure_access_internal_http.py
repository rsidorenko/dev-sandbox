"""ADM-02 ensure-access internal HTTP bridge tests (ASGI, no DB)."""

from __future__ import annotations

import json

import httpx

from app.admin_support.adm02_internal_http import (
    ADM02_INTERNAL_AUDIT_EVENTS_PATH,
    ADM02_INTERNAL_ENSURE_ACCESS_PATH,
    create_adm02_internal_http_app,
)
from app.admin_support.contracts import (
    Adm01SupportAccessReadinessBucket,
    Adm01SupportNextAction,
    Adm01SupportSubscriptionBucket,
    Adm02BillingFactsCategory,
    Adm02BillingFactsDiagnostics,
    Adm02DiagnosticsInput,
    Adm02DiagnosticsOutcome,
    Adm02DiagnosticsResult,
    Adm02DiagnosticsSummary,
    Adm02EnsureAccessInput,
    Adm02EnsureAccessOutcome,
    Adm02EnsureAccessRemediationResult,
    Adm02EnsureAccessResult,
    Adm02EnsureAccessSummary,
    Adm02QuarantineDiagnostics,
    Adm02QuarantineMarker,
    Adm02QuarantineReasonCode,
    Adm02ReconciliationDiagnostics,
    Adm02ReconciliationRunMarker,
    AdminActorRef,
    InternalAdminPrincipalExtractionInput,
    InternalAdminPrincipalExtractionOutcome,
    InternalAdminPrincipalExtractionResult,
    RedactionMarker,
)
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


class _DiagHandler:
    async def handle(self, inp: Adm02DiagnosticsInput) -> Adm02DiagnosticsResult:
        return Adm02DiagnosticsResult(
            outcome=Adm02DiagnosticsOutcome.SUCCESS,
            correlation_id=inp.correlation_id,
            summary=Adm02DiagnosticsSummary(
                billing=Adm02BillingFactsDiagnostics(
                    category=Adm02BillingFactsCategory.NONE,
                    internal_fact_refs=(),
                ),
                quarantine=Adm02QuarantineDiagnostics(
                    marker=Adm02QuarantineMarker.NONE,
                    reason_code=Adm02QuarantineReasonCode.NONE,
                ),
                reconciliation=Adm02ReconciliationDiagnostics(
                    last_run_marker=Adm02ReconciliationRunMarker.NONE,
                ),
                redaction=RedactionMarker.NONE,
            ),
        )


class _EnsureHandler:
    async def handle(self, inp: Adm02EnsureAccessInput) -> Adm02EnsureAccessResult:
        return Adm02EnsureAccessResult(
            outcome=Adm02EnsureAccessOutcome.SUCCESS,
            correlation_id=inp.correlation_id,
            summary=Adm02EnsureAccessSummary(
                telegram_identity_known=True,
                subscription_bucket=Adm01SupportSubscriptionBucket.ACTIVE,
                access_readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
                remediation_result=Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
                recommended_next_action=Adm01SupportNextAction.ASK_USER_TO_USE_GET_ACCESS,
            ),
        )


class _Extractor:
    async def extract_trusted_internal_admin_principal(self, inp: InternalAdminPrincipalExtractionInput):
        return InternalAdminPrincipalExtractionResult(
            outcome=InternalAdminPrincipalExtractionOutcome.SUCCESS,
            principal=AdminActorRef(internal_admin_principal_id="adm-ok"),
        )


async def _post_json(app, path: str, payload: dict):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=payload)


class _AuditLookupHandler:
    async def handle(self, inp):
        _ = inp
        from app.admin_support.contracts import (
            Adm01SupportAccessReadinessBucket,
            Adm02EnsureAccessAuditEventType,
            Adm02EnsureAccessAuditEvidenceItem,
            Adm02EnsureAccessAuditLookupOutcome,
            Adm02EnsureAccessAuditLookupResponse,
            Adm02EnsureAccessAuditOutcomeBucket,
            Adm02EnsureAccessAuditPrincipalMarker,
            Adm02EnsureAccessAuditReadResult,
            Adm02EnsureAccessRemediationResult,
        )

        return Adm02EnsureAccessAuditLookupResponse(
            outcome=Adm02EnsureAccessAuditLookupOutcome.SUCCESS,
            correlation_id=inp.correlation_id,
            result=Adm02EnsureAccessAuditReadResult(
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
            ),
        )


def test_ensure_access_route_success_safe_shape() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        app = create_adm02_internal_http_app(_DiagHandler(), _Extractor(), ensure_access_handler=_EnsureHandler())
        r = await _post_json(
            app,
            ADM02_INTERNAL_ENSURE_ACCESS_PATH,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-x",
                "telegram_user_id": 42,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "success"
        assert body["summary"] is not None
        assert body["summary"]["telegram_identity_known"] is True
        assert body["summary"]["access_readiness_bucket"] == "active_access_ready"
        assert body["summary"]["remediation_result"] == "issued_access"
        payload = json.dumps(body, sort_keys=True).lower()
        for forbidden in (
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
        ):
            assert forbidden not in payload

    _run(main())


def test_audit_events_route_success_safe_shape_when_mutation_handler_absent() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        app = create_adm02_internal_http_app(
            _DiagHandler(),
            _Extractor(),
            ensure_access_handler=None,
            ensure_access_audit_lookup_handler=_AuditLookupHandler(),
        )
        r = await _post_json(
            app,
            ADM02_INTERNAL_AUDIT_EVENTS_PATH,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-x",
                "evidence_correlation_id": "a" * 32,
                "limit": 10,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "success"
        assert isinstance(body["items"], list)
        assert len(body["items"]) == 1
        payload = json.dumps(body, sort_keys=True).lower()
        for forbidden in (
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
        ):
            assert forbidden not in payload

    _run(main())
