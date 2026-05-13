"""ADM-02 internal HTTP composition: Starlette → endpoint → real extractor/allowlist/handler (no network/DB)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime

import httpx

from app.admin_support.adm02_billing_facts_ledger_adapter import Adm02BillingFactsLedgerReadAdapter
from app.admin_support.adm02_diagnostics import ADM02_CAPABILITY_CLASS, Adm02DiagnosticsHandler
from app.admin_support.adm02_fact_of_access_audit_adapter import (
    Adm02FactOfAccessPersistenceAuditAdapter,
)
from app.admin_support.adm02_internal_http import (
    ADM02_INTERNAL_DIAGNOSTICS_PATH,
    create_adm02_internal_http_app,
)
from app.admin_support.adm02_quarantine_mismatch_adapter import Adm02QuarantineMismatchReadAdapter
from app.admin_support.adm02_reconciliation_runs_adapter import Adm02ReconciliationRunsReadAdapter
from app.admin_support.authorization import AllowlistAdm02Authorization
from app.admin_support.contracts import (
    Adm02BillingFactsCategory,
    Adm02DiagnosticsSummary,
    Adm02FactOfAccessDisclosureCategory,
    Adm02QuarantineMarker,
    Adm02QuarantineReasonCode,
    Adm02ReconciliationRunMarker,
    InternalUserTarget,
    RedactionMarker,
)
from app.admin_support.principal_extraction import DefaultInternalAdminPrincipalExtractor
from app.persistence.adm02_fact_of_access import InMemoryAdm02FactOfAccessRecordAppender
from app.persistence.billing_events_ledger_contracts import (
    BillingEventAmountCurrency,
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
)
from app.persistence.billing_events_ledger_in_memory import InMemoryBillingEventsLedgerRepository
from app.persistence.mismatch_quarantine_contracts import (
    MismatchQuarantineReasonCode,
    MismatchQuarantineRecord,
    MismatchQuarantineResolutionStatus,
    MismatchQuarantineSourceType,
)
from app.persistence.mismatch_quarantine_in_memory import InMemoryMismatchQuarantineRepository
from app.persistence.reconciliation_runs_contracts import (
    ReconciliationRunOutcome,
    ReconciliationRunRecord,
    ReconciliationRunStatus,
)
from app.persistence.reconciliation_runs_in_memory import InMemoryReconciliationRunsRepository
from app.shared.correlation import new_correlation_id


def _run(coro):
    return asyncio.run(coro)


_FORBIDDEN_BOUNDARY_FRAGMENTS = (
    "external_event_id",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "database_url",
    "postgres://",
    "postgresql://",
    "bearer ",
    "private key",
    "raw_provider_payload",
)


class _IdentityFake:
    def __init__(self, resolved_internal_user_id: str) -> None:
        self._resolved = resolved_internal_user_id
        self.calls = 0
        self.last_target: object | None = None

    async def resolve_internal_user_id(self, target, *, correlation_id: str) -> str | None:
        self.calls += 1
        self.last_target = target
        return self._resolved


class _RedactionCallsStub:
    def __init__(self) -> None:
        self.calls = 0

    async def redact_diagnostics_summary(self, summary: Adm02DiagnosticsSummary) -> Adm02DiagnosticsSummary:
        self.calls += 1
        return replace(summary, redaction=RedactionMarker.PARTIAL)


class _FailingRedactionStub:
    def __init__(self) -> None:
        self.calls = 0

    async def redact_diagnostics_summary(self, summary: Adm02DiagnosticsSummary) -> Adm02DiagnosticsSummary:
        self.calls += 1
        raise RuntimeError("redaction failure")


def _make_billing_record(
    *,
    internal_fact_ref: str,
    internal_user_id: str,
    external_event_id: str,
    status: BillingEventLedgerStatus = BillingEventLedgerStatus.ACCEPTED,
) -> BillingEventLedgerRecord:
    now = datetime(2026, 4, 16, 0, 0, tzinfo=UTC)
    return BillingEventLedgerRecord(
        internal_fact_ref=internal_fact_ref,
        billing_provider_key="provider-test",
        external_event_id=external_event_id,
        event_type="payment_succeeded",
        event_effective_at=now,
        event_received_at=now,
        internal_user_id=internal_user_id,
        checkout_attempt_id=None,
        amount_currency=BillingEventAmountCurrency(
            amount_minor_units=1000,
            currency_code="USD",
        ),
        status=status,
        ingestion_correlation_id="corr-test",
    )


def _make_mismatch_quarantine_record(
    *,
    record_id: str,
    source_ref_id: str,
    internal_user_id: str | None,
    reason_code: MismatchQuarantineReasonCode,
    created_at: datetime,
    updated_at: datetime,
    resolution_status: MismatchQuarantineResolutionStatus = MismatchQuarantineResolutionStatus.ACTIVE,
) -> MismatchQuarantineRecord:
    return MismatchQuarantineRecord(
        id=record_id,
        source_type=MismatchQuarantineSourceType.RECONCILIATION_RUN,
        source_ref_id=source_ref_id,
        internal_user_id=internal_user_id,
        reason_code=reason_code,
        resolution_status=resolution_status,
        reconciliation_run_id=source_ref_id,
        created_at=created_at,
        updated_at=updated_at,
        resolved_at=None,
        resolved_by_admin_id=None,
    )


def _make_reconciliation_run_record(
    *,
    run_id: str,
    internal_user_id: str | None,
    outcome: ReconciliationRunOutcome,
    started_at: datetime,
    finished_at: datetime | None = None,
    status: ReconciliationRunStatus = ReconciliationRunStatus.COMPLETED,
) -> ReconciliationRunRecord:
    return ReconciliationRunRecord(
        id=run_id,
        internal_user_id=internal_user_id,
        billing_provider_key="provider-test",
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        outcome=outcome,
        created_billing_fact_refs=(),
        correlation_id="corr-recon-test",
    )


class _SpyBillingEventsLedgerRepository(InMemoryBillingEventsLedgerRepository):
    def __init__(self) -> None:
        super().__init__()
        self.summary_calls = 0

    async def get_user_billing_facts_summary(self, internal_user_id: str):
        self.summary_calls += 1
        return await super().get_user_billing_facts_summary(internal_user_id)


class _SpyMismatchQuarantineRepository(InMemoryMismatchQuarantineRepository):
    def __init__(self) -> None:
        super().__init__()
        self.summary_calls = 0

    async def get_user_quarantine_summary(self, internal_user_id: str):
        self.summary_calls += 1
        return await super().get_user_quarantine_summary(internal_user_id)


class _SpyReconciliationRunsRepository(InMemoryReconciliationRunsRepository):
    def __init__(self) -> None:
        super().__init__()
        self.summary_calls = 0

    async def get_user_reconciliation_summary(self, internal_user_id: str):
        self.summary_calls += 1
        return await super().get_user_reconciliation_summary(internal_user_id)


async def _post_json(app, payload: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(ADM02_INTERNAL_DIAGNOSTICS_PATH, json=payload)


def test_adm02_internal_http_composition_happy_path_real_chain() -> None:
    cid = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    identity = _IdentityFake("u-resolved")
    ledger = InMemoryBillingEventsLedgerRepository()
    billing = Adm02BillingFactsLedgerReadAdapter(ledger)
    quarantine_repo = _SpyMismatchQuarantineRepository()
    quarantine = Adm02QuarantineMismatchReadAdapter(quarantine_repo)
    recon_repo = InMemoryReconciliationRunsRepository()
    reconciliation = Adm02ReconciliationRunsReadAdapter(recon_repo)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    audit = Adm02FactOfAccessPersistenceAuditAdapter(
        appender=persisted,
        now_provider=lambda: expected_now,
    )
    handler = Adm02DiagnosticsHandler(
        authorization=AllowlistAdm02Authorization(["adm-allowed"]),
        identity=identity,
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        audit=audit,
        redaction=None,
    )
    app = create_adm02_internal_http_app(handler, DefaultInternalAdminPrincipalExtractor())

    async def main() -> None:
        t0 = datetime(2026, 4, 16, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 4, 16, 11, 0, 0, tzinfo=UTC)
        t_noise = datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC)
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-old",
                internal_user_id="u-resolved",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t0,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-latest",
                internal_user_id="u-resolved",
                outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
                started_at=t1,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-noise",
                internal_user_id="u-other",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t_noise,
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-1",
                internal_user_id="u-resolved",
                external_event_id="evt-1",
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-2",
                internal_user_id="u-resolved",
                external_event_id="evt-2",
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-noise-1",
                internal_user_id="u-other",
                external_event_id="evt-3",
            )
        )
        q_old = datetime(2026, 4, 16, 9, 0, 0, tzinfo=UTC)
        q_new = datetime(2026, 4, 16, 13, 0, 0, tzinfo=UTC)
        q_noise = datetime(2026, 4, 16, 8, 0, 0, tzinfo=UTC)
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="mq-old",
                source_ref_id="src-mismatch",
                internal_user_id="u-resolved",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=q_old,
                updated_at=q_old,
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="mq-new",
                source_ref_id="src-needs-review",
                internal_user_id="u-resolved",
                reason_code=MismatchQuarantineReasonCode.NEEDS_REVIEW,
                created_at=q_new,
                updated_at=q_new,
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="mq-noise",
                source_ref_id="src-noise-other",
                internal_user_id="u-other",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=q_noise,
                updated_at=q_noise,
            )
        )

        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "  adm-allowed  ",
                "internal_user_id": "u-target",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "success"
        assert body["correlation_id"] == cid
        s = body["summary"]
        assert s is not None
        for key in (
            "billing_category",
            "quarantine_marker",
            "quarantine_reason_code",
            "reconciliation_last_run_marker",
            "redaction",
        ):
            assert key in s
            assert isinstance(s[key], str)
        assert isinstance(s["internal_fact_refs"], list)
        assert s["billing_category"] == Adm02BillingFactsCategory.HAS_ACCEPTED.value
        assert s["internal_fact_refs"] == ["be-1", "be-2"]
        assert s["quarantine_marker"] == Adm02QuarantineMarker.ACTIVE.value
        assert s["quarantine_reason_code"] == Adm02QuarantineReasonCode.NEEDS_REVIEW.value
        assert s["reconciliation_last_run_marker"] == Adm02ReconciliationRunMarker.FACTS_DISCOVERED.value
        assert s["redaction"] == "none"
        assert identity.calls == 1
        assert identity.last_target == InternalUserTarget(internal_user_id="u-target")
        assert quarantine_repo.summary_calls == 1
        recorded = await persisted.recorded_for_tests()
        assert len(recorded) == 1
        rec = recorded[0]
        assert rec.occurred_at == expected_now
        assert rec.correlation_id == cid
        assert rec.actor_ref.internal_admin_principal_id == "adm-allowed"
        assert rec.capability_class == ADM02_CAPABILITY_CLASS
        assert rec.internal_user_scope_ref == "u-resolved"
        assert rec.disclosure is Adm02FactOfAccessDisclosureCategory.UNREDACTED

    _run(main())


def test_adm02_internal_http_composition_success_boundary_never_leaks_sensitive_internal_fragments() -> None:
    cid = new_correlation_id()
    identity = _IdentityFake("u-resolved")
    ledger = InMemoryBillingEventsLedgerRepository()
    billing = Adm02BillingFactsLedgerReadAdapter(ledger)
    quarantine_repo = InMemoryMismatchQuarantineRepository()
    quarantine = Adm02QuarantineMismatchReadAdapter(quarantine_repo)
    recon_repo = InMemoryReconciliationRunsRepository()
    reconciliation = Adm02ReconciliationRunsReadAdapter(recon_repo)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    audit = Adm02FactOfAccessPersistenceAuditAdapter(
        appender=persisted,
        now_provider=lambda: datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC),
    )
    handler = Adm02DiagnosticsHandler(
        authorization=AllowlistAdm02Authorization(["adm-allowed"]),
        identity=identity,
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        audit=audit,
        redaction=None,
    )
    app = create_adm02_internal_http_app(handler, DefaultInternalAdminPrincipalExtractor())

    async def main() -> None:
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-safe-1",
                internal_user_id="u-resolved",
                external_event_id=(
                    "external_event_id/provider_issuance_ref/issue_idempotency_key/"
                    "DATABASE_URL/postgres://postgresql://Bearer PRIVATE KEY/raw_provider_payload"
                ),
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="mq-sensitive-source",
                source_ref_id="raw_provider_payload/provider_issuance_ref/issue_idempotency_key",
                internal_user_id="u-resolved",
                reason_code=MismatchQuarantineReasonCode.NEEDS_REVIEW,
                created_at=datetime(2026, 4, 16, 13, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 16, 13, 0, 0, tzinfo=UTC),
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="external_event_id/raw_provider_payload",
                internal_user_id="u-resolved",
                outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
                started_at=datetime(2026, 4, 16, 11, 0, 0, tzinfo=UTC),
            )
        )

        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-allowed",
                "internal_user_id": "u-target",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "success"
        assert body["summary"] is not None
        assert body["summary"]["internal_fact_refs"] == ["be-safe-1"]

        payload_lower = json.dumps(body, sort_keys=True).lower()
        for forbidden in _FORBIDDEN_BOUNDARY_FRAGMENTS:
            assert forbidden not in payload_lower
        assert "internal_fact_refs" in payload_lower
        assert identity.calls == 1

    _run(main())


def test_adm02_internal_http_composition_redaction_partial_success_real_chain() -> None:
    cid = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    identity = _IdentityFake("u-resolved")
    redaction = _RedactionCallsStub()
    ledger = InMemoryBillingEventsLedgerRepository()
    billing = Adm02BillingFactsLedgerReadAdapter(ledger)
    quarantine_repo = InMemoryMismatchQuarantineRepository()
    quarantine = Adm02QuarantineMismatchReadAdapter(quarantine_repo)
    recon_repo = InMemoryReconciliationRunsRepository()
    reconciliation = Adm02ReconciliationRunsReadAdapter(recon_repo)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    audit = Adm02FactOfAccessPersistenceAuditAdapter(
        appender=persisted,
        now_provider=lambda: expected_now,
    )
    handler = Adm02DiagnosticsHandler(
        authorization=AllowlistAdm02Authorization(["adm-allowed"]),
        identity=identity,
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        audit=audit,
        redaction=redaction,
    )
    app = create_adm02_internal_http_app(handler, DefaultInternalAdminPrincipalExtractor())

    async def main() -> None:
        t0 = datetime(2026, 4, 16, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 4, 16, 11, 0, 0, tzinfo=UTC)
        t_noise = datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC)
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-old",
                internal_user_id="u-resolved",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t0,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-latest",
                internal_user_id="u-resolved",
                outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
                started_at=t1,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-noise",
                internal_user_id="u-other",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t_noise,
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-1",
                internal_user_id="u-resolved",
                external_event_id="evt-1",
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-2",
                internal_user_id="u-resolved",
                external_event_id="evt-2",
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-noise-1",
                internal_user_id="u-other",
                external_event_id="evt-3",
            )
        )
        q_old = datetime(2026, 4, 16, 9, 0, 0, tzinfo=UTC)
        q_new = datetime(2026, 4, 16, 13, 0, 0, tzinfo=UTC)
        q_noise = datetime(2026, 4, 16, 8, 0, 0, tzinfo=UTC)
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="mq-old",
                source_ref_id="src-mismatch",
                internal_user_id="u-resolved",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=q_old,
                updated_at=q_old,
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="mq-new",
                source_ref_id="src-needs-review",
                internal_user_id="u-resolved",
                reason_code=MismatchQuarantineReasonCode.NEEDS_REVIEW,
                created_at=q_new,
                updated_at=q_new,
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="mq-noise",
                source_ref_id="src-noise-other",
                internal_user_id="u-other",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=q_noise,
                updated_at=q_noise,
            )
        )

        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "  adm-allowed  ",
                "internal_user_id": "u-target",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "success"
        assert body["correlation_id"] == cid
        s = body["summary"]
        assert s is not None
        assert s["redaction"] == RedactionMarker.PARTIAL.value
        assert s["billing_category"] == Adm02BillingFactsCategory.HAS_ACCEPTED.value
        assert s["internal_fact_refs"] == ["be-1", "be-2"]
        assert s["quarantine_marker"] == Adm02QuarantineMarker.ACTIVE.value
        assert s["quarantine_reason_code"] == Adm02QuarantineReasonCode.NEEDS_REVIEW.value
        assert s["reconciliation_last_run_marker"] == Adm02ReconciliationRunMarker.FACTS_DISCOVERED.value
        assert redaction.calls == 1
        assert identity.calls == 1
        recorded = await persisted.recorded_for_tests()
        assert len(recorded) == 1
        rec = recorded[0]
        assert rec.actor_ref.internal_admin_principal_id == "adm-allowed"
        assert rec.internal_user_scope_ref == "u-resolved"
        assert rec.correlation_id == cid
        assert rec.disclosure is Adm02FactOfAccessDisclosureCategory.PARTIAL

    _run(main())


def test_adm02_internal_http_composition_redaction_and_audit_disclosure_stay_low_cardinality() -> None:
    cid = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    identity = _IdentityFake("u-resolved")
    redaction = _RedactionCallsStub()
    ledger = InMemoryBillingEventsLedgerRepository()
    billing = Adm02BillingFactsLedgerReadAdapter(ledger)
    quarantine_repo = InMemoryMismatchQuarantineRepository()
    quarantine = Adm02QuarantineMismatchReadAdapter(quarantine_repo)
    recon_repo = InMemoryReconciliationRunsRepository()
    reconciliation = Adm02ReconciliationRunsReadAdapter(recon_repo)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    audit = Adm02FactOfAccessPersistenceAuditAdapter(
        appender=persisted,
        now_provider=lambda: expected_now,
    )
    handler = Adm02DiagnosticsHandler(
        authorization=AllowlistAdm02Authorization(["adm-allowed"]),
        identity=identity,
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        audit=audit,
        redaction=redaction,
    )
    app = create_adm02_internal_http_app(handler, DefaultInternalAdminPrincipalExtractor())

    async def main() -> None:
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-safe-1",
                internal_user_id="u-resolved",
                external_event_id="postgres://raw_provider_payload",
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="mq-1",
                source_ref_id="provider_issuance_ref",
                internal_user_id="u-resolved",
                reason_code=MismatchQuarantineReasonCode.NEEDS_REVIEW,
                created_at=datetime(2026, 4, 16, 13, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 16, 13, 0, 0, tzinfo=UTC),
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="issue_idempotency_key",
                internal_user_id="u-resolved",
                outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
                started_at=datetime(2026, 4, 16, 11, 0, 0, tzinfo=UTC),
            )
        )

        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-allowed",
                "internal_user_id": "u-target",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "success"
        assert body["summary"] is not None
        assert body["summary"]["redaction"] in {marker.value for marker in RedactionMarker}
        payload_lower = json.dumps(body, sort_keys=True).lower()
        for forbidden in _FORBIDDEN_BOUNDARY_FRAGMENTS:
            assert forbidden not in payload_lower

        recorded = await persisted.recorded_for_tests()
        assert len(recorded) == 1
        disclosure_value = recorded[0].disclosure.value
        assert disclosure_value in {category.value for category in Adm02FactOfAccessDisclosureCategory}
        assert disclosure_value in {"unredacted", "partial", "fully_redacted"}
        disclosure_lower = disclosure_value.lower()
        for forbidden in _FORBIDDEN_BOUNDARY_FRAGMENTS:
            assert forbidden not in disclosure_lower

    _run(main())


def test_adm02_internal_http_composition_deny_short_circuits_before_ports() -> None:
    cid = new_correlation_id()
    identity = _IdentityFake("u-should-not-run")
    ledger = _SpyBillingEventsLedgerRepository()
    billing = Adm02BillingFactsLedgerReadAdapter(ledger)
    quarantine_repo = _SpyMismatchQuarantineRepository()
    quarantine = Adm02QuarantineMismatchReadAdapter(quarantine_repo)
    recon_repo = _SpyReconciliationRunsRepository()
    reconciliation = Adm02ReconciliationRunsReadAdapter(recon_repo)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    audit = Adm02FactOfAccessPersistenceAuditAdapter(
        appender=persisted,
        now_provider=lambda: datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
    )
    handler = Adm02DiagnosticsHandler(
        authorization=AllowlistAdm02Authorization(["adm-only-other"]),
        identity=identity,
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        audit=audit,
        redaction=None,
    )
    app = create_adm02_internal_http_app(handler, DefaultInternalAdminPrincipalExtractor())

    async def main() -> None:
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-not-allowlisted",
                "internal_user_id": "u-target",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "denied"
        assert body["correlation_id"] == cid
        assert body["summary"] is None
        assert identity.calls == 0
        assert ledger.summary_calls == 0
        assert quarantine_repo.summary_calls == 0
        assert recon_repo.summary_calls == 0
        recorded = await persisted.recorded_for_tests()
        assert recorded == ()

    _run(main())


def test_adm02_internal_http_composition_redaction_denied_short_circuits() -> None:
    cid = new_correlation_id()
    identity = _IdentityFake("u-should-not-run")
    ledger = _SpyBillingEventsLedgerRepository()
    billing = Adm02BillingFactsLedgerReadAdapter(ledger)
    quarantine_repo = _SpyMismatchQuarantineRepository()
    quarantine = Adm02QuarantineMismatchReadAdapter(quarantine_repo)
    recon_repo = _SpyReconciliationRunsRepository()
    reconciliation = Adm02ReconciliationRunsReadAdapter(recon_repo)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    audit = Adm02FactOfAccessPersistenceAuditAdapter(
        appender=persisted,
        now_provider=lambda: datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
    )
    redaction = _RedactionCallsStub()
    handler = Adm02DiagnosticsHandler(
        authorization=AllowlistAdm02Authorization(["adm-only-other"]),
        identity=identity,
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        audit=audit,
        redaction=redaction,
    )
    app = create_adm02_internal_http_app(handler, DefaultInternalAdminPrincipalExtractor())

    async def main() -> None:
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "adm-not-allowlisted",
                "internal_user_id": "u-target",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "denied"
        assert body["correlation_id"] == cid
        assert body["summary"] is None
        assert identity.calls == 0
        assert redaction.calls == 0
        assert ledger.summary_calls == 0
        assert quarantine_repo.summary_calls == 0
        assert recon_repo.summary_calls == 0
        recorded = await persisted.recorded_for_tests()
        assert recorded == ()

    _run(main())


def test_adm02_internal_http_composition_redaction_failure_is_fail_closed() -> None:
    cid = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    identity = _IdentityFake("u-resolved")
    ledger = _SpyBillingEventsLedgerRepository()
    billing = Adm02BillingFactsLedgerReadAdapter(ledger)
    quarantine_repo = _SpyMismatchQuarantineRepository()
    quarantine = Adm02QuarantineMismatchReadAdapter(quarantine_repo)
    recon_repo = _SpyReconciliationRunsRepository()
    reconciliation = Adm02ReconciliationRunsReadAdapter(recon_repo)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    audit = Adm02FactOfAccessPersistenceAuditAdapter(
        appender=persisted,
        now_provider=lambda: expected_now,
    )
    redaction = _FailingRedactionStub()
    handler = Adm02DiagnosticsHandler(
        authorization=AllowlistAdm02Authorization(["adm-allowed"]),
        identity=identity,
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        audit=audit,
        redaction=redaction,
    )
    app = create_adm02_internal_http_app(handler, DefaultInternalAdminPrincipalExtractor())

    async def main() -> None:
        t0 = datetime(2026, 4, 16, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 4, 16, 11, 0, 0, tzinfo=UTC)
        t_noise = datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC)
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-old",
                internal_user_id="u-resolved",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t0,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-latest",
                internal_user_id="u-resolved",
                outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
                started_at=t1,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-noise",
                internal_user_id="u-other",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t_noise,
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-1",
                internal_user_id="u-resolved",
                external_event_id="evt-1",
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-2",
                internal_user_id="u-resolved",
                external_event_id="evt-2",
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-noise-1",
                internal_user_id="u-other",
                external_event_id="evt-3",
            )
        )
        q_old = datetime(2026, 4, 16, 9, 0, 0, tzinfo=UTC)
        q_new = datetime(2026, 4, 16, 13, 0, 0, tzinfo=UTC)
        q_noise = datetime(2026, 4, 16, 8, 0, 0, tzinfo=UTC)
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="mq-old",
                source_ref_id="src-mismatch",
                internal_user_id="u-resolved",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=q_old,
                updated_at=q_old,
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="mq-new",
                source_ref_id="src-needs-review",
                internal_user_id="u-resolved",
                reason_code=MismatchQuarantineReasonCode.NEEDS_REVIEW,
                created_at=q_new,
                updated_at=q_new,
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="mq-noise",
                source_ref_id="src-noise-other",
                internal_user_id="u-other",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=q_noise,
                updated_at=q_noise,
            )
        )

        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "  adm-allowed  ",
                "internal_user_id": "u-target",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["correlation_id"] == cid
        assert body["outcome"] == "dependency_failure"
        assert body["summary"] is None
        assert identity.calls == 1
        assert redaction.calls == 1
        assert ledger.summary_calls == 1
        assert quarantine_repo.summary_calls == 1
        assert recon_repo.summary_calls == 1
        assert await persisted.recorded_for_tests() == ()

    _run(main())
