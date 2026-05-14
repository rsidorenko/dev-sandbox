"""ADM-01 thin transport adapter tests (fake handler; no network/DB)."""

from __future__ import annotations

import json
from dataclasses import asdict

from app.admin_support.adm01_endpoint import Adm01InboundRequest, execute_adm01_endpoint
from app.admin_support.contracts import (
    Adm01LookupInput,
    Adm01LookupOutcome,
    Adm01LookupResult,
    Adm01LookupSummary,
    Adm01SubscriptionStatusSummary,
    Adm01SupportAccessReadinessBucket,
    Adm01SupportNextAction,
    Adm01SupportReadinessSummary,
    Adm01SupportSubscriptionBucket,
    AdminActorRef,
    AdminPolicyFlag,
    EntitlementSummary,
    EntitlementSummaryCategory,
    InternalAdminPrincipalExtractionInput,
    InternalAdminPrincipalExtractionOutcome,
    InternalAdminPrincipalExtractionResult,
    InternalUserTarget,
    IssuanceOperationalState,
    IssuanceOperationalSummary,
    RedactionMarker,
    TelegramUserTarget,
)
from app.admin_support.principal_extraction import DefaultInternalAdminPrincipalExtractor
from app.application.interfaces import SubscriptionSnapshot
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


def _req(
    *,
    cid: str | None = None,
    internal_admin_principal_id: str = "adm-test",
    internal_user_id: str | None = None,
    telegram_user_id: int | None = None,
) -> Adm01InboundRequest:
    return Adm01InboundRequest(
        correlation_id=cid if cid is not None else new_correlation_id(),
        internal_admin_principal_id=internal_admin_principal_id,
        internal_user_id=internal_user_id,
        telegram_user_id=telegram_user_id,
    )


def _lookup_result(
    outcome: Adm01LookupOutcome,
    correlation_id: str,
    *,
    summary: Adm01LookupSummary | None = None,
) -> Adm01LookupResult:
    return Adm01LookupResult(outcome=outcome, correlation_id=correlation_id, summary=summary)


def _success_result(cid: str) -> Adm01LookupResult:
    return Adm01LookupResult(
        outcome=Adm01LookupOutcome.SUCCESS,
        correlation_id=cid,
        summary=Adm01LookupSummary(
            subscription=Adm01SubscriptionStatusSummary(
                snapshot=SubscriptionSnapshot(internal_user_id="u-1", state_label="active"),
            ),
            entitlement=EntitlementSummary(category=EntitlementSummaryCategory.ACTIVE),
            policy_flag=AdminPolicyFlag.DEFAULT,
            issuance=IssuanceOperationalSummary(state=IssuanceOperationalState.OK),
            support_readiness=Adm01SupportReadinessSummary(
                telegram_identity_known=True,
                subscription_bucket=Adm01SupportSubscriptionBucket.ACTIVE,
                access_readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
                recommended_next_action=Adm01SupportNextAction.ASK_USER_TO_USE_GET_ACCESS,
            ),
            redaction=RedactionMarker.NONE,
        ),
    )


class _RecordingHandler:
    def __init__(self, result: Adm01LookupResult) -> None:
        self._result = result
        self.last_inp: Adm01LookupInput | None = None

    async def handle(self, inp: Adm01LookupInput) -> Adm01LookupResult:
        self.last_inp = inp
        return self._result


class _NeverCalledHandler:
    async def handle(self, inp: Adm01LookupInput) -> Adm01LookupResult:
        raise AssertionError("handler must not be called")


class _ExplodingHandler:
    async def handle(self, inp: Adm01LookupInput) -> Adm01LookupResult:
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


def test_endpoint_with_real_extractor_trims_principal_and_calls_handler() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _RecordingHandler(_success_result(cid))
        r = await execute_adm01_endpoint(
            h,
            DefaultInternalAdminPrincipalExtractor(),
            _req(
                cid=cid,
                internal_admin_principal_id="  adm-trimmed  ",
                internal_user_id="u-42",
            ),
        )
        assert h.last_inp is not None
        assert h.last_inp.actor == AdminActorRef(internal_admin_principal_id="adm-trimmed")
        assert h.last_inp.target == InternalUserTarget(internal_user_id="u-42")
        assert r.outcome == Adm01LookupOutcome.SUCCESS.value

    _run(main())


def test_endpoint_with_real_extractor_blank_principal_returns_invalid_input() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        r = await execute_adm01_endpoint(
            _NeverCalledHandler(),
            DefaultInternalAdminPrincipalExtractor(),
            _req(cid=cid, internal_admin_principal_id="   ", internal_user_id="u-1"),
        )
        assert r.outcome == Adm01LookupOutcome.INVALID_INPUT.value
        assert r.correlation_id == cid
        assert r.summary is None

    _run(main())


def test_endpoint_with_real_extractor_tab_newline_principal_returns_invalid_input() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        r = await execute_adm01_endpoint(
            _NeverCalledHandler(),
            DefaultInternalAdminPrincipalExtractor(),
            _req(cid=cid, internal_admin_principal_id="\t\n", internal_user_id="u-1"),
        )
        assert r.outcome == Adm01LookupOutcome.INVALID_INPUT.value
        assert r.correlation_id == cid
        assert r.summary is None

    _run(main())


def test_endpoint_invalid_internal_admin_principal_id_never_calls_handler() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _NeverCalledHandler()
        for principal in ("", "   ", "\t"):
            r = await execute_adm01_endpoint(
                h,
                _NonSuccessExtractor(InternalAdminPrincipalExtractionOutcome.MALFORMED_PRINCIPAL),
                _req(cid=cid, internal_admin_principal_id=principal, internal_user_id="u-1"),
            )
            assert r.outcome == Adm01LookupOutcome.INVALID_INPUT.value
            assert r.summary is None

    _run(main())


def test_endpoint_invalid_telegram_user_id_never_calls_handler() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _NeverCalledHandler()
        for tg in (0, -1, True):
            r = await execute_adm01_endpoint(
                h,
                _SuccessExtractor(),
                _req(cid=cid, telegram_user_id=tg),  # type: ignore[arg-type]
            )
            assert r.outcome == Adm01LookupOutcome.INVALID_INPUT.value
            assert r.summary is None

    _run(main())


def test_endpoint_invalid_internal_user_id_format_never_calls_handler() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _NeverCalledHandler()
        for uid in ("", "  u-1  "):
            r = await execute_adm01_endpoint(h, _SuccessExtractor(), _req(cid=cid, internal_user_id=uid))
            assert r.outcome == Adm01LookupOutcome.INVALID_INPUT.value
            assert r.summary is None

    _run(main())


def test_endpoint_handler_exception_maps_to_dependency_failure_safe_response() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        r = await execute_adm01_endpoint(
            _ExplodingHandler(),
            _SuccessExtractor(),
            _req(cid=cid, internal_user_id="u-ok"),
        )
        assert r.outcome == Adm01LookupOutcome.DEPENDENCY_FAILURE.value
        assert r.correlation_id == cid
        assert r.summary is None

    _run(main())


def test_endpoint_invalid_input_preserves_request_correlation_id() -> None:
    raw_cid = new_correlation_id()

    async def main() -> None:
        h = _NeverCalledHandler()
        r = await execute_adm01_endpoint(
            h,
            _NonSuccessExtractor(InternalAdminPrincipalExtractionOutcome.MISSING_PRINCIPAL),
            _req(cid=raw_cid, internal_admin_principal_id="", internal_user_id="u-1"),
        )
        assert r.correlation_id == raw_cid
        assert r.outcome == Adm01LookupOutcome.INVALID_INPUT.value

    _run(main())


def test_endpoint_internal_user_id_normalized_target() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        extracted_actor_id = "adm-normalized"
        h = _RecordingHandler(_success_result(cid))
        r = await execute_adm01_endpoint(
            h,
            _SuccessExtractor(actor_id=extracted_actor_id),
            _req(cid=cid, internal_user_id="u-42"),
        )
        assert h.last_inp is not None
        assert h.last_inp.target == InternalUserTarget(internal_user_id="u-42")
        assert h.last_inp.actor == AdminActorRef(internal_admin_principal_id=extracted_actor_id)
        assert h.last_inp.correlation_id == cid
        assert r.outcome == Adm01LookupOutcome.SUCCESS.value
        assert r.summary is not None
        assert r.summary.subscription_bucket == "active"

    _run(main())


def test_endpoint_telegram_user_id_normalized_target() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _RecordingHandler(_success_result(cid))
        r = await execute_adm01_endpoint(h, _SuccessExtractor(), _req(cid=cid, telegram_user_id=424242))
        assert h.last_inp is not None
        assert h.last_inp.target == TelegramUserTarget(telegram_user_id=424242)
        assert r.outcome == Adm01LookupOutcome.SUCCESS.value
        assert r.summary is not None

    _run(main())


def test_endpoint_invalid_target_combination_safe_outcome() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _NeverCalledHandler()
        r_none = await execute_adm01_endpoint(
            h,
            _SuccessExtractor(),
            _req(cid=cid, internal_user_id=None, telegram_user_id=None),
        )
        assert r_none.outcome == Adm01LookupOutcome.INVALID_INPUT.value
        r_both = await execute_adm01_endpoint(
            h,
            _SuccessExtractor(),
            _req(cid=cid, internal_user_id="x", telegram_user_id=1),
        )
        assert r_both.outcome == Adm01LookupOutcome.INVALID_INPUT.value

    _run(main())


def test_endpoint_success_exposes_summary_shape() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _RecordingHandler(_success_result(cid))
        r = await execute_adm01_endpoint(h, _SuccessExtractor(), _req(cid=cid, internal_user_id="u-1"))
        assert r.correlation_id == cid
        assert r.summary is not None
        s = r.summary
        assert s.telegram_identity_known is True
        assert s.subscription_bucket == Adm01SupportSubscriptionBucket.ACTIVE.value
        assert s.access_readiness_bucket == Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY.value
        assert s.recommended_next_action == Adm01SupportNextAction.ASK_USER_TO_USE_GET_ACCESS.value
        assert s.redaction == RedactionMarker.NONE.value

    _run(main())


def test_endpoint_denied_passthrough_from_handler_result() -> None:
    req_cid = new_correlation_id()
    handler_cid = new_correlation_id()

    async def main() -> None:
        h = _RecordingHandler(_lookup_result(Adm01LookupOutcome.DENIED, handler_cid))
        r = await execute_adm01_endpoint(
            h,
            _SuccessExtractor(),
            _req(cid=req_cid, internal_user_id="u-1"),
        )
        assert r.outcome == Adm01LookupOutcome.DENIED.value
        assert r.summary is None
        assert r.correlation_id == handler_cid

    _run(main())


def test_endpoint_target_not_resolved_passthrough_preserves_result_correlation_id() -> None:
    req_cid = new_correlation_id()
    handler_cid = new_correlation_id()

    async def main() -> None:
        h = _RecordingHandler(_lookup_result(Adm01LookupOutcome.TARGET_NOT_RESOLVED, handler_cid))
        r = await execute_adm01_endpoint(
            h,
            _SuccessExtractor(),
            _req(cid=req_cid, telegram_user_id=99),
        )
        assert r.outcome == Adm01LookupOutcome.TARGET_NOT_RESOLVED.value
        assert r.summary is None
        assert r.correlation_id == handler_cid

    _run(main())


def test_endpoint_dependency_failure_result_passthrough_not_exception_path() -> None:
    req_cid = new_correlation_id()
    handler_cid = new_correlation_id()

    async def main() -> None:
        h = _RecordingHandler(_lookup_result(Adm01LookupOutcome.DEPENDENCY_FAILURE, handler_cid))
        r = await execute_adm01_endpoint(
            h,
            _SuccessExtractor(),
            _req(cid=req_cid, internal_user_id="u-z"),
        )
        assert r.outcome == Adm01LookupOutcome.DEPENDENCY_FAILURE.value
        assert r.summary is None
        assert r.correlation_id == handler_cid

    _run(main())


def test_endpoint_success_without_summary_does_not_fabricate_summary() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _RecordingHandler(_lookup_result(Adm01LookupOutcome.SUCCESS, cid, summary=None))
        r = await execute_adm01_endpoint(
            h,
            _SuccessExtractor(),
            _req(cid=cid, internal_user_id="u-1"),
        )
        assert r.outcome == Adm01LookupOutcome.SUCCESS.value
        assert r.summary is None

    _run(main())


def test_endpoint_extractor_non_success_short_circuits_to_invalid_input() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        r = await execute_adm01_endpoint(
            _NeverCalledHandler(),
            _NonSuccessExtractor(InternalAdminPrincipalExtractionOutcome.UNTRUSTED_SOURCE),
            _req(cid=cid, internal_user_id="u-1"),
        )
        assert r.outcome == Adm01LookupOutcome.INVALID_INPUT.value
        assert r.correlation_id == cid
        assert r.summary is None

    _run(main())


def test_endpoint_extractor_exception_short_circuits_to_dependency_failure() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        r = await execute_adm01_endpoint(
            _NeverCalledHandler(),
            _ExplodingExtractor(),
            _req(cid=cid, internal_user_id="u-1"),
        )
        assert r.outcome == Adm01LookupOutcome.DEPENDENCY_FAILURE.value
        assert r.correlation_id == cid
        assert r.summary is None

    _run(main())


def test_endpoint_response_contract_low_cardinality_stable_and_denied_redacted() -> None:
    cid_success = new_correlation_id()
    cid_deny = new_correlation_id()
    expected_top_level_keys = {"outcome", "correlation_id", "summary"}
    expected_summary_keys = {
        "telegram_identity_known",
        "subscription_bucket",
        "access_readiness_bucket",
        "recommended_next_action",
        "redaction",
    }

    async def main() -> None:
        success_resp = await execute_adm01_endpoint(
            _RecordingHandler(_success_result(cid_success)),
            _SuccessExtractor(),
            _req(cid=cid_success, internal_user_id="u-1"),
        )
        success_dict = asdict(success_resp)
        assert set(success_dict.keys()) == expected_top_level_keys
        assert success_resp.outcome == Adm01LookupOutcome.SUCCESS.value
        assert success_resp.correlation_id == cid_success
        assert success_resp.summary is not None

        summary = success_resp.summary
        assert summary is not None
        summary_dict = asdict(summary)
        assert set(summary_dict.keys()) == expected_summary_keys
        assert summary.telegram_identity_known is True
        assert summary.subscription_bucket in {v.value for v in Adm01SupportSubscriptionBucket}
        assert summary.access_readiness_bucket in {v.value for v in Adm01SupportAccessReadinessBucket}
        assert summary.recommended_next_action in {v.value for v in Adm01SupportNextAction}
        assert summary.redaction in {v.value for v in RedactionMarker}

        denied_resp = await execute_adm01_endpoint(
            _RecordingHandler(_lookup_result(Adm01LookupOutcome.DENIED, cid_deny)),
            _SuccessExtractor(),
            _req(cid=cid_deny, internal_user_id="u-1"),
        )
        denied_dict = asdict(denied_resp)
        assert set(denied_dict.keys()) == expected_top_level_keys
        assert denied_resp.outcome == Adm01LookupOutcome.DENIED.value
        assert denied_resp.correlation_id == cid_deny
        assert denied_resp.summary is None

        combined_repr = (
            json.dumps(success_dict, sort_keys=True)
            + repr(success_resp)
            + json.dumps(denied_dict, sort_keys=True)
            + repr(denied_resp)
        )
        forbidden_fragments = (
            "provider_issuance_ref",
            "issue_idempotency_key",
            "internal_user_id",
            "checkout_attempt_id",
            "customer_ref",
            "provider_ref",
            "schema_version",
            "BEGIN ",
            "token=",
            "vpn://",
            "internal_fact_ref",
            "external_event_id",
            "DATABASE_URL",
            "postgres://",
            "postgresql://",
            "Bearer ",
            "PRIVATE KEY",
            "provider_payload",
            "billing_payload",
            "raw_provider_payload",
            "raw_billing_payload",
        )
        for fragment in forbidden_fragments:
            assert fragment not in combined_repr

    _run(main())
