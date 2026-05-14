"""ADM-02 internal HTTP bridge: Starlette → execute_adm02_endpoint (no network/DB)."""

from __future__ import annotations

import json

import httpx

from app.admin_support.adm02_internal_http import (
    ADM02_INTERNAL_DIAGNOSTICS_PATH,
    create_adm02_internal_http_app,
)
from app.admin_support.contracts import (
    Adm02BillingFactsCategory,
    Adm02BillingFactsDiagnostics,
    Adm02DiagnosticsInput,
    Adm02DiagnosticsOutcome,
    Adm02DiagnosticsResult,
    Adm02DiagnosticsSummary,
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

_TOP_LEVEL_KEYS = frozenset({"outcome", "correlation_id", "summary"})
_SUMMARY_KEYS = frozenset(
    {
        "billing_category",
        "internal_fact_refs",
        "quarantine_marker",
        "quarantine_reason_code",
        "reconciliation_last_run_marker",
        "redaction",
    }
)
_FORBIDDEN_FRAGMENTS = (
    "database_url",
    "postgres://",
    "postgresql://",
    "bearer ",
    "private key",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "external_event_id",
    "raw_payload",
    "raw_webhook",
)


def _full_summary() -> Adm02DiagnosticsSummary:
    return Adm02DiagnosticsSummary(
        billing=Adm02BillingFactsDiagnostics(
            category=Adm02BillingFactsCategory.HAS_ACCEPTED,
            internal_fact_refs=("ref-a", "ref-b"),
        ),
        quarantine=Adm02QuarantineDiagnostics(
            marker=Adm02QuarantineMarker.NONE,
            reason_code=Adm02QuarantineReasonCode.NONE,
        ),
        reconciliation=Adm02ReconciliationDiagnostics(
            last_run_marker=Adm02ReconciliationRunMarker.NO_CHANGES,
        ),
        redaction=RedactionMarker.NONE,
    )


def _success_result(cid: str, summary: Adm02DiagnosticsSummary | None) -> Adm02DiagnosticsResult:
    return Adm02DiagnosticsResult(
        outcome=Adm02DiagnosticsOutcome.SUCCESS,
        correlation_id=cid,
        summary=summary,
    )


class _RecordingHandler:
    def __init__(self, result: Adm02DiagnosticsResult) -> None:
        self._result = result

    async def handle(self, inp: Adm02DiagnosticsInput) -> Adm02DiagnosticsResult:
        return self._result


class _ExplodingHandler:
    async def handle(self, inp: Adm02DiagnosticsInput) -> Adm02DiagnosticsResult:
        raise RuntimeError("must not surface")


class _SuccessExtractor:
    def __init__(self, actor_id: str = "adm-extracted") -> None:
        self._actor = AdminActorRef(internal_admin_principal_id=actor_id)

    async def extract_trusted_internal_admin_principal(
        self,
        inp: InternalAdminPrincipalExtractionInput,
    ) -> InternalAdminPrincipalExtractionResult:
        return InternalAdminPrincipalExtractionResult(
            outcome=InternalAdminPrincipalExtractionOutcome.SUCCESS,
            principal=self._actor,
        )


async def _post_json(app, payload):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(ADM02_INTERNAL_DIAGNOSTICS_PATH, json=payload)


async def _post_raw(app, content: bytes, content_type: str = "application/json"):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            ADM02_INTERNAL_DIAGNOSTICS_PATH,
            content=content,
            headers={"Content-Type": content_type},
        )


def test_http_happy_path_success_summary() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        app = create_adm02_internal_http_app(
            _RecordingHandler(_success_result(cid, _full_summary())),
            _SuccessExtractor(),
        )
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-1",
                "internal_user_id": "u-42",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "success"
        assert body["correlation_id"] == cid
        s = body["summary"]
        assert s is not None
        assert s["billing_category"] == "has_accepted"
        assert s["internal_fact_refs"] == ["ref-a", "ref-b"]
        assert s["quarantine_marker"] == "none"
        assert s["quarantine_reason_code"] == "none"
        assert s["reconciliation_last_run_marker"] == "no_changes"
        assert s["redaction"] == "none"

    _run(main())


def test_http_success_summary_null() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        app = create_adm02_internal_http_app(
            _RecordingHandler(_success_result(cid, None)),
            _SuccessExtractor(),
        )
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-1",
                "internal_user_id": "u-42",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "success"
        assert body["summary"] is None

    _run(main())


def test_http_denied_passthrough() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        app = create_adm02_internal_http_app(
            _RecordingHandler(
                Adm02DiagnosticsResult(
                    outcome=Adm02DiagnosticsOutcome.DENIED,
                    correlation_id=cid,
                    summary=None,
                ),
            ),
            _SuccessExtractor(),
        )
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-1",
                "internal_user_id": "u-1",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "denied"
        assert body["summary"] is None

    _run(main())


def test_http_target_not_resolved_passthrough() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        app = create_adm02_internal_http_app(
            _RecordingHandler(
                Adm02DiagnosticsResult(
                    outcome=Adm02DiagnosticsOutcome.TARGET_NOT_RESOLVED,
                    correlation_id=cid,
                    summary=None,
                ),
            ),
            _SuccessExtractor(),
        )
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-1",
                "internal_user_id": "u-1",
            },
        )
        assert r.status_code == 200
        assert r.json()["outcome"] == "target_not_resolved"
        assert r.json()["summary"] is None

    _run(main())


def test_http_dependency_failure_from_handler() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        app = create_adm02_internal_http_app(
            _RecordingHandler(
                Adm02DiagnosticsResult(
                    outcome=Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE,
                    correlation_id=cid,
                    summary=None,
                ),
            ),
            _SuccessExtractor(),
        )
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-1",
                "internal_user_id": "u-1",
            },
        )
        assert r.status_code == 200
        assert r.json()["outcome"] == "dependency_failure"
        assert r.json()["summary"] is None

    _run(main())


def test_http_handler_exception_dependency_failure_safe_body() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        app = create_adm02_internal_http_app(
            _ExplodingHandler(),
            _SuccessExtractor(),
        )
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-1",
                "internal_user_id": "u-1",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "dependency_failure"
        assert body["correlation_id"] == cid
        assert body["summary"] is None
        assert "traceback" not in body
        assert "RuntimeError" not in str(body)

    _run(main())


def test_http_dependency_failure_exception_message_never_leaks_sensitive_fragments() -> None:
    cid = new_correlation_id()
    sensitive_error = (
        "traceback RuntimeError external_event_id provider_issuance_ref "
        "issue_idempotency_key DATABASE_URL postgres:// postgresql:// "
        "Bearer SECRET PRIVATE KEY raw_provider_payload"
    )

    class _SensitiveExplodingHandler:
        async def handle(self, inp: Adm02DiagnosticsInput) -> Adm02DiagnosticsResult:
            raise RuntimeError(sensitive_error)

    async def main() -> None:
        app = create_adm02_internal_http_app(
            _SensitiveExplodingHandler(),
            _SuccessExtractor(),
        )
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-1",
                "internal_user_id": "u-1",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "dependency_failure"
        assert body["correlation_id"] == cid
        assert body["summary"] is None

        payload_lower = json.dumps(body, sort_keys=True).lower()
        for forbidden in _FORBIDDEN_FRAGMENTS:
            assert forbidden not in payload_lower
        assert "raw_provider_payload" not in payload_lower
        assert "traceback" not in payload_lower
        assert "runtimeerror" not in payload_lower

    _run(main())


def test_http_invalid_json_400() -> None:
    async def main() -> None:
        cid = new_correlation_id()
        app = create_adm02_internal_http_app(
            _RecordingHandler(_success_result(cid, _full_summary())),
            _SuccessExtractor(),
        )
        r = await _post_raw(app, b"{not json", "application/json")
        assert r.status_code == 400
        assert r.json() == {"error": "invalid_json"}

    _run(main())


def test_http_non_object_json_400() -> None:
    async def main() -> None:
        cid = new_correlation_id()
        app = create_adm02_internal_http_app(
            _RecordingHandler(_success_result(cid, _full_summary())),
            _SuccessExtractor(),
        )
        r = await _post_raw(app, b"[]", "application/json")
        assert r.status_code == 400
        assert r.json() == {"error": "invalid_body"}

    _run(main())


def test_http_contract_locked_boundary_and_no_unexpected_fragments() -> None:
    cid = new_correlation_id()
    allowed_outcomes = {item.value for item in Adm02DiagnosticsOutcome}
    allowed_billing_categories = {item.value for item in Adm02BillingFactsCategory}
    allowed_quarantine_markers = {item.value for item in Adm02QuarantineMarker}
    allowed_quarantine_reasons = {item.value for item in Adm02QuarantineReasonCode}
    allowed_reconciliation_markers = {item.value for item in Adm02ReconciliationRunMarker}
    allowed_redaction_markers = {item.value for item in RedactionMarker}

    async def main() -> None:
        app_success = create_adm02_internal_http_app(
            _RecordingHandler(_success_result(cid, _full_summary())),
            _SuccessExtractor(),
        )
        success_response = await _post_json(
            app_success,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-1",
                "internal_user_id": "u-42",
            },
        )
        assert success_response.status_code == 200
        success_body = success_response.json()
        assert set(success_body.keys()) == _TOP_LEVEL_KEYS
        assert success_body["outcome"] in allowed_outcomes
        assert success_body["correlation_id"] == cid
        assert isinstance(success_body["summary"], dict)
        assert set(success_body["summary"].keys()) == _SUMMARY_KEYS
        assert success_body["summary"]["billing_category"] in allowed_billing_categories
        assert success_body["summary"]["quarantine_marker"] in allowed_quarantine_markers
        assert success_body["summary"]["quarantine_reason_code"] in allowed_quarantine_reasons
        assert success_body["summary"]["reconciliation_last_run_marker"] in allowed_reconciliation_markers
        assert success_body["summary"]["redaction"] in allowed_redaction_markers

        success_payload = json.dumps(success_body, sort_keys=True).lower()
        for forbidden in _FORBIDDEN_FRAGMENTS:
            assert forbidden not in success_payload

        for outcome in (
            Adm02DiagnosticsOutcome.DENIED,
            Adm02DiagnosticsOutcome.INVALID_INPUT,
            Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE,
        ):
            app_non_success = create_adm02_internal_http_app(
                _RecordingHandler(
                    Adm02DiagnosticsResult(
                        outcome=outcome,
                        correlation_id=cid,
                        summary=_full_summary(),
                    )
                ),
                _SuccessExtractor(),
            )
            non_success_response = await _post_json(
                app_non_success,
                {
                    "correlation_id": cid,
                    "internal_admin_principal_id": "adm-1",
                    "internal_user_id": "u-1",
                },
            )
            assert non_success_response.status_code == 200
            non_success_body = non_success_response.json()
            assert set(non_success_body.keys()) == _TOP_LEVEL_KEYS
            assert non_success_body["outcome"] == outcome.value
            assert non_success_body["correlation_id"] == cid
            assert non_success_body["summary"] is None
            assert "traceback" not in non_success_body
            payload_lower = json.dumps(non_success_body, sort_keys=True).lower()
            for forbidden in _FORBIDDEN_FRAGMENTS:
                assert forbidden not in payload_lower

    _run(main())
