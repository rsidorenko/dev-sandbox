"""ADM-02 thin transport adapter tests (fake handler; no network/DB)."""

from __future__ import annotations

import json
from dataclasses import asdict

from app.admin_support.adm02_endpoint import Adm02InboundRequest, execute_adm02_endpoint
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
    InternalUserTarget,
    RedactionMarker,
    TelegramUserTarget,
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


def _req(
    *,
    cid: str | None = None,
    internal_admin_principal_id: str = "adm-test",
    internal_user_id: str | None = None,
    telegram_user_id: int | None = None,
) -> Adm02InboundRequest:
    return Adm02InboundRequest(
        correlation_id=cid if cid is not None else new_correlation_id(),
        internal_admin_principal_id=internal_admin_principal_id,
        internal_user_id=internal_user_id,
        telegram_user_id=telegram_user_id,
    )


def _diag_result(
    outcome: Adm02DiagnosticsOutcome,
    correlation_id: str,
    *,
    summary: Adm02DiagnosticsSummary | None = None,
) -> Adm02DiagnosticsResult:
    return Adm02DiagnosticsResult(outcome=outcome, correlation_id=correlation_id, summary=summary)


def _success_summary() -> Adm02DiagnosticsSummary:
    return Adm02DiagnosticsSummary(
        billing=Adm02BillingFactsDiagnostics(
            category=Adm02BillingFactsCategory.HAS_ACCEPTED,
            internal_fact_refs=("fact-1", "fact-2"),
        ),
        quarantine=Adm02QuarantineDiagnostics(
            marker=Adm02QuarantineMarker.ACTIVE,
            reason_code=Adm02QuarantineReasonCode.NEEDS_REVIEW,
        ),
        reconciliation=Adm02ReconciliationDiagnostics(
            last_run_marker=Adm02ReconciliationRunMarker.FACTS_DISCOVERED,
        ),
        redaction=RedactionMarker.PARTIAL,
    )


class _RecordingHandler:
    def __init__(self, result: Adm02DiagnosticsResult) -> None:
        self._result = result
        self.last_inp: Adm02DiagnosticsInput | None = None

    async def handle(self, inp: Adm02DiagnosticsInput) -> Adm02DiagnosticsResult:
        self.last_inp = inp
        return self._result


class _NeverCalledHandler:
    async def handle(self, inp: Adm02DiagnosticsInput) -> Adm02DiagnosticsResult:
        raise AssertionError("handler must not be called")


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


class _NonSuccessExtractor:
    def __init__(self, outcome: InternalAdminPrincipalExtractionOutcome) -> None:
        self._outcome = outcome

    async def extract_trusted_internal_admin_principal(
        self,
        inp: InternalAdminPrincipalExtractionInput,
    ) -> InternalAdminPrincipalExtractionResult:
        return InternalAdminPrincipalExtractionResult(outcome=self._outcome, principal=None)


class _ExplodingExtractor:
    async def extract_trusted_internal_admin_principal(
        self,
        inp: InternalAdminPrincipalExtractionInput,
    ) -> InternalAdminPrincipalExtractionResult:
        raise RuntimeError("extractor failure")


def test_adm02_success_internal_user_target_normalized_input_and_summary() -> None:
    cid = new_correlation_id()
    summ = _success_summary()

    async def main() -> None:
        h = _RecordingHandler(_diag_result(Adm02DiagnosticsOutcome.SUCCESS, cid, summary=summ))
        r = await execute_adm02_endpoint(h, _SuccessExtractor(), _req(cid=cid, internal_user_id="u-42"))
        assert h.last_inp is not None
        assert h.last_inp.actor == AdminActorRef(internal_admin_principal_id="adm-extracted")
        assert h.last_inp.target == InternalUserTarget(internal_user_id="u-42")
        assert h.last_inp.correlation_id == cid
        assert r.outcome == Adm02DiagnosticsOutcome.SUCCESS.value
        assert r.correlation_id == cid
        assert r.summary is not None
        assert r.summary.billing_category == Adm02BillingFactsCategory.HAS_ACCEPTED.value
        assert r.summary.internal_fact_refs == ("fact-1", "fact-2")
        assert r.summary.quarantine_marker == Adm02QuarantineMarker.ACTIVE.value
        assert r.summary.quarantine_reason_code == Adm02QuarantineReasonCode.NEEDS_REVIEW.value
        assert r.summary.reconciliation_last_run_marker == Adm02ReconciliationRunMarker.FACTS_DISCOVERED.value
        assert r.summary.redaction == RedactionMarker.PARTIAL.value

    _run(main())


def test_adm02_success_telegram_user_target() -> None:
    cid = new_correlation_id()
    summ = _success_summary()

    async def main() -> None:
        h = _RecordingHandler(_diag_result(Adm02DiagnosticsOutcome.SUCCESS, cid, summary=summ))
        r = await execute_adm02_endpoint(h, _SuccessExtractor(), _req(cid=cid, telegram_user_id=424242))
        assert h.last_inp is not None
        assert h.last_inp.target == TelegramUserTarget(telegram_user_id=424242)
        assert r.outcome == Adm02DiagnosticsOutcome.SUCCESS.value
        assert r.summary is not None

    _run(main())


def test_adm02_invalid_target_combination_handler_not_called() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _NeverCalledHandler()
        r_none = await execute_adm02_endpoint(
            h,
            _SuccessExtractor(),
            _req(cid=cid, internal_user_id=None, telegram_user_id=None),
        )
        assert r_none.outcome == Adm02DiagnosticsOutcome.INVALID_INPUT.value
        r_both = await execute_adm02_endpoint(
            h,
            _SuccessExtractor(),
            _req(cid=cid, internal_user_id="x", telegram_user_id=1),
        )
        assert r_both.outcome == Adm02DiagnosticsOutcome.INVALID_INPUT.value

    _run(main())


def test_adm02_extractor_non_success_invalid_input_handler_not_called() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        r = await execute_adm02_endpoint(
            _NeverCalledHandler(),
            _NonSuccessExtractor(InternalAdminPrincipalExtractionOutcome.MALFORMED_PRINCIPAL),
            _req(cid=cid, internal_user_id="u-1"),
        )
        assert r.outcome == Adm02DiagnosticsOutcome.INVALID_INPUT.value
        assert r.correlation_id == cid
        assert r.summary is None

    _run(main())


def test_adm02_extractor_exception_dependency_failure_handler_not_called() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        r = await execute_adm02_endpoint(
            _NeverCalledHandler(),
            _ExplodingExtractor(),
            _req(cid=cid, internal_user_id="u-1"),
        )
        assert r.outcome == Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE.value
        assert r.correlation_id == cid
        assert r.summary is None

    _run(main())


def test_adm02_handler_exception_dependency_failure() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        r = await execute_adm02_endpoint(
            _ExplodingHandler(),
            _SuccessExtractor(),
            _req(cid=cid, internal_user_id="u-ok"),
        )
        assert r.outcome == Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE.value
        assert r.correlation_id == cid
        assert r.summary is None

    _run(main())


def test_adm02_denied_passthrough_correlation_from_handler_result() -> None:
    req_cid = new_correlation_id()
    handler_cid = new_correlation_id()

    async def main() -> None:
        h = _RecordingHandler(_diag_result(Adm02DiagnosticsOutcome.DENIED, handler_cid))
        r = await execute_adm02_endpoint(h, _SuccessExtractor(), _req(cid=req_cid, internal_user_id="u-1"))
        assert r.outcome == Adm02DiagnosticsOutcome.DENIED.value
        assert r.summary is None
        assert r.correlation_id == handler_cid

    _run(main())


def test_adm02_target_not_resolved_passthrough() -> None:
    req_cid = new_correlation_id()
    handler_cid = new_correlation_id()

    async def main() -> None:
        h = _RecordingHandler(_diag_result(Adm02DiagnosticsOutcome.TARGET_NOT_RESOLVED, handler_cid))
        r = await execute_adm02_endpoint(h, _SuccessExtractor(), _req(cid=req_cid, telegram_user_id=99))
        assert r.outcome == Adm02DiagnosticsOutcome.TARGET_NOT_RESOLVED.value
        assert r.summary is None
        assert r.correlation_id == handler_cid

    _run(main())


def test_adm02_success_without_summary_response_summary_none() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _RecordingHandler(_diag_result(Adm02DiagnosticsOutcome.SUCCESS, cid, summary=None))
        r = await execute_adm02_endpoint(h, _SuccessExtractor(), _req(cid=cid, internal_user_id="u-1"))
        assert r.outcome == Adm02DiagnosticsOutcome.SUCCESS.value
        assert r.summary is None

    _run(main())


def test_adm02_endpoint_contract_locked_boundary_and_no_unexpected_fragments() -> None:
    cid = new_correlation_id()
    summ = _success_summary()
    allowed_outcomes = {item.value for item in Adm02DiagnosticsOutcome}
    allowed_billing_categories = {item.value for item in Adm02BillingFactsCategory}
    allowed_quarantine_markers = {item.value for item in Adm02QuarantineMarker}
    allowed_quarantine_reasons = {item.value for item in Adm02QuarantineReasonCode}
    allowed_reconciliation_markers = {item.value for item in Adm02ReconciliationRunMarker}
    allowed_redaction_markers = {item.value for item in RedactionMarker}

    async def main() -> None:
        h_success = _RecordingHandler(_diag_result(Adm02DiagnosticsOutcome.SUCCESS, cid, summary=summ))
        r_success = await execute_adm02_endpoint(
            h_success,
            _SuccessExtractor(),
            _req(cid=cid, internal_user_id="u-42"),
        )
        encoded_success = json.dumps(asdict(r_success), sort_keys=True)
        encoded_success_lower = encoded_success.lower()
        success_dict = asdict(r_success)
        assert set(success_dict.keys()) == _TOP_LEVEL_KEYS
        assert success_dict["outcome"] in allowed_outcomes
        assert success_dict["correlation_id"] == cid
        assert isinstance(success_dict["summary"], dict)
        assert set(success_dict["summary"].keys()) == _SUMMARY_KEYS
        assert success_dict["summary"]["billing_category"] in allowed_billing_categories
        assert success_dict["summary"]["quarantine_marker"] in allowed_quarantine_markers
        assert success_dict["summary"]["quarantine_reason_code"] in allowed_quarantine_reasons
        assert success_dict["summary"]["reconciliation_last_run_marker"] in allowed_reconciliation_markers
        assert success_dict["summary"]["redaction"] in allowed_redaction_markers

        for forbidden in _FORBIDDEN_FRAGMENTS:
            assert forbidden not in encoded_success_lower

        for outcome in (
            Adm02DiagnosticsOutcome.DENIED,
            Adm02DiagnosticsOutcome.INVALID_INPUT,
            Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE,
        ):
            h_non_success = _RecordingHandler(_diag_result(outcome, cid, summary=summ))
            r_non_success = await execute_adm02_endpoint(
                h_non_success,
                _SuccessExtractor(),
                _req(cid=cid, internal_user_id="u-42"),
            )
            non_success_dict = asdict(r_non_success)
            assert set(non_success_dict.keys()) == _TOP_LEVEL_KEYS
            assert non_success_dict["outcome"] == outcome.value
            assert non_success_dict["correlation_id"] == cid
            assert non_success_dict["summary"] is None
            encoded_non_success = json.dumps(non_success_dict, sort_keys=True).lower()
            for forbidden in _FORBIDDEN_FRAGMENTS:
                assert forbidden not in encoded_non_success

    _run(main())


def test_adm02_endpoint_success_leak_guard_sensitive_internal_strings_do_not_cross_boundary() -> None:
    cid = new_correlation_id()
    sensitive_markers = (
        "external_event_id",
        "provider_issuance_ref",
        "issue_idempotency_key",
        "DATABASE_URL",
        "postgres://",
        "postgresql://",
        "Bearer ",
        "PRIVATE KEY",
        "raw_provider_payload",
    )
    summary = Adm02DiagnosticsSummary(
        billing=Adm02BillingFactsDiagnostics(
            category=Adm02BillingFactsCategory.HAS_ACCEPTED,
            internal_fact_refs=("opaque-fact-1", "opaque-fact-2"),
        ),
        quarantine=Adm02QuarantineDiagnostics(
            marker=Adm02QuarantineMarker.ACTIVE,
            reason_code=Adm02QuarantineReasonCode.NEEDS_REVIEW,
        ),
        reconciliation=Adm02ReconciliationDiagnostics(
            last_run_marker=Adm02ReconciliationRunMarker.FACTS_DISCOVERED,
        ),
        redaction=RedactionMarker.PARTIAL,
    )

    async def main() -> None:
        h = _RecordingHandler(_diag_result(Adm02DiagnosticsOutcome.SUCCESS, cid, summary=summary))
        response = await execute_adm02_endpoint(
            h,
            _SuccessExtractor(),
            _req(cid=cid, internal_user_id="u-guard"),
        )
        encoded = json.dumps(asdict(response), sort_keys=True)
        encoded_lower = encoded.lower()
        assert response.outcome == Adm02DiagnosticsOutcome.SUCCESS.value
        assert response.summary is not None
        assert response.summary.internal_fact_refs == ("opaque-fact-1", "opaque-fact-2")
        assert "internal_fact_refs" in encoded_lower
        for fragment in sensitive_markers:
            assert fragment.lower() not in encoded_lower

    _run(main())


def test_adm02_endpoint_handler_exception_leak_guard_dependency_failure_safe_repr() -> None:
    cid = new_correlation_id()
    sensitive_error = (
        "traceback RuntimeError external_event_id provider_issuance_ref "
        "issue_idempotency_key DATABASE_URL postgres:// postgresql:// "
        "Bearer PRIVATE KEY raw_provider_payload"
    )

    class _SensitiveExplodingHandler:
        async def handle(self, inp: Adm02DiagnosticsInput) -> Adm02DiagnosticsResult:
            raise RuntimeError(sensitive_error)

    async def main() -> None:
        response = await execute_adm02_endpoint(
            _SensitiveExplodingHandler(),
            _SuccessExtractor(),
            _req(cid=cid, internal_user_id="u-guard"),
        )
        encoded_lower = json.dumps(asdict(response), sort_keys=True).lower()
        assert response.outcome == Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE.value
        assert response.correlation_id == cid
        assert response.summary is None
        for forbidden in (
            "traceback",
            "runtimeerror",
            "external_event_id",
            "provider_issuance_ref",
            "issue_idempotency_key",
            "database_url",
            "postgres://",
            "postgresql://",
            "bearer ",
            "private key",
            "raw_provider_payload",
        ):
            assert forbidden not in encoded_lower

    _run(main())
