"""ADM-02 bundle helper: Starlette type and diagnostics route path (no transport)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from app.admin_support.adm02_billing_facts_ledger_adapter import Adm02BillingFactsLedgerReadAdapter
from app.admin_support.adm02_diagnostics import ADM02_CAPABILITY_CLASS
from app.admin_support.adm02_internal_http import ADM02_INTERNAL_DIAGNOSTICS_PATH
from app.admin_support.adm02_quarantine_mismatch_adapter import Adm02QuarantineMismatchReadAdapter
from app.admin_support.adm02_reconciliation_runs_adapter import Adm02ReconciliationRunsReadAdapter
from app.admin_support.contracts import (
    Adm02BillingFactsCategory,
    Adm02BillingFactsDiagnostics,
    Adm02DiagnosticsSummary,
    Adm02FactOfAccessDisclosureCategory,
    Adm02QuarantineDiagnostics,
    Adm02QuarantineMarker,
    Adm02QuarantineReasonCode,
    Adm02ReconciliationDiagnostics,
    Adm02ReconciliationRunMarker,
    RedactionMarker,
)
from app.internal_admin.adm02_bundle import (
    Adm02InternalDiagnosticsDependencies,
    Adm02InternalDiagnosticsPersistenceAuditDependencies,
    Adm02InternalDiagnosticsPersistenceBackedDependencies,
    build_adm02_internal_diagnostics_starlette_app,
    build_adm02_internal_diagnostics_starlette_app_with_persistence_audit,
    build_adm02_internal_diagnostics_starlette_app_with_persistence_backing,
)
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

_FORBIDDEN_BUNDLE_FRAGMENTS = (
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


def _run(coro):
    return asyncio.run(coro)


class _IdentityStub:
    async def resolve_internal_user_id(self, target, *, correlation_id: str) -> str | None:
        return "u1"


class _BillingStub:
    async def get_billing_facts_diagnostics(self, internal_user_id: str) -> Adm02BillingFactsDiagnostics:
        return Adm02BillingFactsDiagnostics(
            category=Adm02BillingFactsCategory.NONE,
            internal_fact_refs=(),
        )


class _QuarantineStub:
    async def get_quarantine_diagnostics(self, internal_user_id: str) -> Adm02QuarantineDiagnostics:
        return Adm02QuarantineDiagnostics(
            marker=Adm02QuarantineMarker.NONE,
            reason_code=Adm02QuarantineReasonCode.NONE,
        )


class _ReconciliationStub:
    async def get_reconciliation_diagnostics(self, internal_user_id: str) -> Adm02ReconciliationDiagnostics:
        return Adm02ReconciliationDiagnostics(last_run_marker=Adm02ReconciliationRunMarker.NONE)


class _AuditStub:
    async def append_fact_of_access(self, record) -> None:
        return None


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


def test_build_adm02_internal_diagnostics_starlette_app_type_and_path() -> None:
    deps = Adm02InternalDiagnosticsDependencies(
        identity=_IdentityStub(),
        billing=_BillingStub(),
        quarantine=_QuarantineStub(),
        reconciliation=_ReconciliationStub(),
        audit=_AuditStub(),
        redaction=None,
        adm02_allowlisted_internal_admin_principal_ids=("p1",),
    )
    app = build_adm02_internal_diagnostics_starlette_app(deps)
    assert isinstance(app, Starlette)
    route_paths = [r.path for r in app.routes if isinstance(r, Route)]
    assert route_paths == [ADM02_INTERNAL_DIAGNOSTICS_PATH]


def test_build_adm02_internal_diagnostics_starlette_app_with_persistence_audit_persists_record() -> None:
    correlation_id = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    ledger = InMemoryBillingEventsLedgerRepository()
    billing = Adm02BillingFactsLedgerReadAdapter(ledger)
    quarantine_repo = _SpyMismatchQuarantineRepository()
    quarantine = Adm02QuarantineMismatchReadAdapter(quarantine_repo)
    recon_repo = InMemoryReconciliationRunsRepository()
    reconciliation = Adm02ReconciliationRunsReadAdapter(recon_repo)
    deps = Adm02InternalDiagnosticsPersistenceAuditDependencies(
        identity=_IdentityStub(),
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        fact_of_access_appender=persisted,
        now_provider=lambda: expected_now,
        redaction=None,
        adm02_allowlisted_internal_admin_principal_ids=("p1",),
    )
    app = build_adm02_internal_diagnostics_starlette_app_with_persistence_audit(deps)

    async def main() -> None:
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-1",
                internal_user_id="u1",
                external_event_id="evt-1",
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-2",
                internal_user_id="u1",
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

        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-u1-mismatch",
                source_ref_id="run-u1-mismatch",
                internal_user_id="u1",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC),
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-u1-review",
                source_ref_id="run-u1-review",
                internal_user_id="u1",
                reason_code=MismatchQuarantineReasonCode.NEEDS_REVIEW,
                created_at=datetime(2026, 4, 15, 11, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 16, 9, 0, 0, tzinfo=UTC),
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-other",
                source_ref_id="run-other",
                internal_user_id="u-other",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=datetime(2026, 4, 14, 0, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 14, 0, 0, 0, tzinfo=UTC),
            )
        )

        t0 = datetime(2026, 4, 16, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 4, 16, 11, 0, 0, tzinfo=UTC)
        t_noise = datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC)
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-u1-old",
                internal_user_id="u1",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t0,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-u1-latest",
                internal_user_id="u1",
                outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
                started_at=t1,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-other-noise",
                internal_user_id="u-other",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t_noise,
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                ADM02_INTERNAL_DIAGNOSTICS_PATH,
                json={
                    "correlation_id": correlation_id,
                    "internal_admin_principal_id": "  p1  ",
                    "internal_user_id": "u-target",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "success"
        assert body["summary"] is not None
        summary = body["summary"]
        assert summary is not None
        assert summary["billing_category"] == Adm02BillingFactsCategory.HAS_ACCEPTED.value
        assert summary["internal_fact_refs"] == ["be-1", "be-2"]
        assert summary["quarantine_marker"] == Adm02QuarantineMarker.ACTIVE.value
        assert summary["quarantine_reason_code"] == Adm02QuarantineReasonCode.NEEDS_REVIEW.value
        assert summary["reconciliation_last_run_marker"] == Adm02ReconciliationRunMarker.FACTS_DISCOVERED.value
        assert quarantine_repo.summary_calls == 1

        recorded = await persisted.recorded_for_tests()
        assert len(recorded) == 1
        record = recorded[0]
        assert record.occurred_at == expected_now
        assert record.correlation_id == correlation_id
        assert record.actor_ref.internal_admin_principal_id == "p1"
        assert record.capability_class == ADM02_CAPABILITY_CLASS
        assert record.internal_user_scope_ref == "u1"
        assert record.disclosure is Adm02FactOfAccessDisclosureCategory.UNREDACTED

    _run(main())


def test_build_adm02_internal_diagnostics_starlette_app_with_persistence_backing_wires_repos() -> None:
    correlation_id = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    ledger = InMemoryBillingEventsLedgerRepository()
    quarantine_repo = InMemoryMismatchQuarantineRepository()
    recon_repo = InMemoryReconciliationRunsRepository()
    deps = Adm02InternalDiagnosticsPersistenceBackedDependencies(
        identity=_IdentityStub(),
        billing_ledger_repository=ledger,
        mismatch_quarantine_repository=quarantine_repo,
        reconciliation_runs_repository=recon_repo,
        fact_of_access_appender=persisted,
        now_provider=lambda: expected_now,
        redaction=None,
        adm02_allowlisted_internal_admin_principal_ids=("p1",),
    )
    app = build_adm02_internal_diagnostics_starlette_app_with_persistence_backing(deps)

    async def main() -> None:
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-1",
                internal_user_id="u1",
                external_event_id="evt-1",
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-2",
                internal_user_id="u1",
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

        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-u1-mismatch",
                source_ref_id="run-u1-mismatch",
                internal_user_id="u1",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC),
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-u1-review",
                source_ref_id="run-u1-review",
                internal_user_id="u1",
                reason_code=MismatchQuarantineReasonCode.NEEDS_REVIEW,
                created_at=datetime(2026, 4, 15, 11, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 16, 9, 0, 0, tzinfo=UTC),
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-other",
                source_ref_id="run-other",
                internal_user_id="u-other",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=datetime(2026, 4, 14, 0, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 14, 0, 0, 0, tzinfo=UTC),
            )
        )

        t0 = datetime(2026, 4, 16, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 4, 16, 11, 0, 0, tzinfo=UTC)
        t_noise = datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC)
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-u1-old",
                internal_user_id="u1",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t0,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-u1-latest",
                internal_user_id="u1",
                outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
                started_at=t1,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-other-noise",
                internal_user_id="u-other",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t_noise,
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                ADM02_INTERNAL_DIAGNOSTICS_PATH,
                json={
                    "correlation_id": correlation_id,
                    "internal_admin_principal_id": "  p1  ",
                    "internal_user_id": "u-target",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "success"
        assert body["summary"] is not None
        summary = body["summary"]
        assert summary is not None
        assert summary["billing_category"] == Adm02BillingFactsCategory.HAS_ACCEPTED.value
        assert summary["internal_fact_refs"] == ["be-1", "be-2"]
        assert summary["quarantine_marker"] == Adm02QuarantineMarker.ACTIVE.value
        assert summary["quarantine_reason_code"] == Adm02QuarantineReasonCode.NEEDS_REVIEW.value
        assert summary["reconciliation_last_run_marker"] == Adm02ReconciliationRunMarker.FACTS_DISCOVERED.value

        recorded = await persisted.recorded_for_tests()
        assert len(recorded) == 1
        record = recorded[0]
        assert record.occurred_at == expected_now
        assert record.correlation_id == correlation_id
        assert record.actor_ref.internal_admin_principal_id == "p1"
        assert record.capability_class == ADM02_CAPABILITY_CLASS
        assert record.internal_user_scope_ref == "u1"
        assert record.disclosure is Adm02FactOfAccessDisclosureCategory.UNREDACTED

    _run(main())


def test_adm02_bundle_persistence_backed_response_never_leaks_sensitive_internal_fragments() -> None:
    correlation_id = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    ledger = InMemoryBillingEventsLedgerRepository()
    quarantine_repo = InMemoryMismatchQuarantineRepository()
    recon_repo = InMemoryReconciliationRunsRepository()
    deps = Adm02InternalDiagnosticsPersistenceBackedDependencies(
        identity=_IdentityStub(),
        billing_ledger_repository=ledger,
        mismatch_quarantine_repository=quarantine_repo,
        reconciliation_runs_repository=recon_repo,
        fact_of_access_appender=persisted,
        now_provider=lambda: expected_now,
        redaction=None,
        adm02_allowlisted_internal_admin_principal_ids=("p1",),
    )
    app = build_adm02_internal_diagnostics_starlette_app_with_persistence_backing(deps)

    async def main() -> None:
        sensitive_blob = (
            "external_event_id/provider_issuance_ref/issue_idempotency_key/"
            "DATABASE_URL/postgres://postgresql://Bearer PRIVATE KEY/raw_provider_payload"
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="opaque-fact-1",
                internal_user_id="u1",
                external_event_id=sensitive_blob,
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-sensitive",
                source_ref_id=sensitive_blob,
                internal_user_id="u1",
                reason_code=MismatchQuarantineReasonCode.NEEDS_REVIEW,
                created_at=datetime(2026, 4, 15, 11, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 16, 9, 0, 0, tzinfo=UTC),
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id=sensitive_blob,
                internal_user_id="u1",
                outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
                started_at=datetime(2026, 4, 16, 11, 0, 0, tzinfo=UTC),
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                ADM02_INTERNAL_DIAGNOSTICS_PATH,
                json={
                    "correlation_id": correlation_id,
                    "internal_admin_principal_id": "p1",
                    "internal_user_id": "u-target",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "success"
        assert body["summary"] is not None
        summary = body["summary"]
        assert summary is not None
        assert summary["internal_fact_refs"] == ["opaque-fact-1"]
        assert set(summary.keys()) == {
            "billing_category",
            "internal_fact_refs",
            "quarantine_marker",
            "quarantine_reason_code",
            "reconciliation_last_run_marker",
            "redaction",
        }
        assert all(ref.startswith("opaque-") for ref in summary["internal_fact_refs"])

        payload_lower = json.dumps(body, sort_keys=True).lower()
        response_text_lower = response.text.lower()
        body_repr_lower = repr(body).lower()
        for forbidden in _FORBIDDEN_BUNDLE_FRAGMENTS:
            assert forbidden not in payload_lower
            assert forbidden not in response_text_lower
            assert forbidden not in body_repr_lower
        assert "internal_fact_refs" in payload_lower

    _run(main())


def test_build_adm02_internal_diagnostics_starlette_app_with_persistence_backing_redaction_partial_success() -> None:
    correlation_id = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    ledger = InMemoryBillingEventsLedgerRepository()
    quarantine_repo = InMemoryMismatchQuarantineRepository()
    recon_repo = InMemoryReconciliationRunsRepository()
    redaction = _RedactionCallsStub()
    deps = Adm02InternalDiagnosticsPersistenceBackedDependencies(
        identity=_IdentityStub(),
        billing_ledger_repository=ledger,
        mismatch_quarantine_repository=quarantine_repo,
        reconciliation_runs_repository=recon_repo,
        fact_of_access_appender=persisted,
        now_provider=lambda: expected_now,
        redaction=redaction,
        adm02_allowlisted_internal_admin_principal_ids=("p1",),
    )
    app = build_adm02_internal_diagnostics_starlette_app_with_persistence_backing(deps)

    async def main() -> None:
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-1",
                internal_user_id="u1",
                external_event_id="evt-1",
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-2",
                internal_user_id="u1",
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

        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-u1-mismatch",
                source_ref_id="run-u1-mismatch",
                internal_user_id="u1",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC),
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-u1-review",
                source_ref_id="run-u1-review",
                internal_user_id="u1",
                reason_code=MismatchQuarantineReasonCode.NEEDS_REVIEW,
                created_at=datetime(2026, 4, 15, 11, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 16, 9, 0, 0, tzinfo=UTC),
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-other",
                source_ref_id="run-other",
                internal_user_id="u-other",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=datetime(2026, 4, 14, 0, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 14, 0, 0, 0, tzinfo=UTC),
            )
        )

        t0 = datetime(2026, 4, 16, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 4, 16, 11, 0, 0, tzinfo=UTC)
        t_noise = datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC)
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-u1-old",
                internal_user_id="u1",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t0,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-u1-latest",
                internal_user_id="u1",
                outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
                started_at=t1,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-other-noise",
                internal_user_id="u-other",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t_noise,
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                ADM02_INTERNAL_DIAGNOSTICS_PATH,
                json={
                    "correlation_id": correlation_id,
                    "internal_admin_principal_id": "  p1  ",
                    "internal_user_id": "u-target",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "success"
        assert body["summary"] is not None
        assert body["summary"]["redaction"] == RedactionMarker.PARTIAL.value
        assert redaction.calls == 1

        recorded = await persisted.recorded_for_tests()
        assert len(recorded) == 1
        record = recorded[0]
        assert record.actor_ref.internal_admin_principal_id == "p1"
        assert record.correlation_id == correlation_id
        assert record.disclosure is Adm02FactOfAccessDisclosureCategory.PARTIAL

    _run(main())


def test_build_adm02_internal_diagnostics_starlette_app_with_persistence_backing_redaction_failure_is_fail_closed() -> (
    None
):
    correlation_id = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    ledger = InMemoryBillingEventsLedgerRepository()
    quarantine_repo = InMemoryMismatchQuarantineRepository()
    recon_repo = InMemoryReconciliationRunsRepository()
    redaction = _FailingRedactionStub()
    deps = Adm02InternalDiagnosticsPersistenceBackedDependencies(
        identity=_IdentityStub(),
        billing_ledger_repository=ledger,
        mismatch_quarantine_repository=quarantine_repo,
        reconciliation_runs_repository=recon_repo,
        fact_of_access_appender=persisted,
        now_provider=lambda: expected_now,
        redaction=redaction,
        adm02_allowlisted_internal_admin_principal_ids=("p1",),
    )
    app = build_adm02_internal_diagnostics_starlette_app_with_persistence_backing(deps)

    async def main() -> None:
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-1",
                internal_user_id="u1",
                external_event_id="evt-1",
            )
        )
        await ledger.append_or_get_by_provider_and_external_id(
            _make_billing_record(
                internal_fact_ref="be-2",
                internal_user_id="u1",
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

        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-u1-mismatch",
                source_ref_id="run-u1-mismatch",
                internal_user_id="u1",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC),
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-u1-review",
                source_ref_id="run-u1-review",
                internal_user_id="u1",
                reason_code=MismatchQuarantineReasonCode.NEEDS_REVIEW,
                created_at=datetime(2026, 4, 15, 11, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 16, 9, 0, 0, tzinfo=UTC),
            )
        )
        await quarantine_repo.upsert_by_source(
            _make_mismatch_quarantine_record(
                record_id="q-other",
                source_ref_id="run-other",
                internal_user_id="u-other",
                reason_code=MismatchQuarantineReasonCode.MISMATCH,
                created_at=datetime(2026, 4, 14, 0, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 14, 0, 0, 0, tzinfo=UTC),
            )
        )

        t0 = datetime(2026, 4, 16, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 4, 16, 11, 0, 0, tzinfo=UTC)
        t_noise = datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC)
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-u1-old",
                internal_user_id="u1",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t0,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-u1-latest",
                internal_user_id="u1",
                outcome=ReconciliationRunOutcome.FACTS_DISCOVERED,
                started_at=t1,
            )
        )
        await recon_repo.append_run_record(
            _make_reconciliation_run_record(
                run_id="recon-other-noise",
                internal_user_id="u-other",
                outcome=ReconciliationRunOutcome.NO_CHANGES,
                started_at=t_noise,
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                ADM02_INTERNAL_DIAGNOSTICS_PATH,
                json={
                    "correlation_id": correlation_id,
                    "internal_admin_principal_id": "  p1  ",
                    "internal_user_id": "u-target",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["correlation_id"] == correlation_id
        assert body["outcome"] == "dependency_failure"
        assert body["summary"] is None
        assert redaction.calls == 1
        assert await persisted.recorded_for_tests() == ()

    _run(main())


def test_build_adm02_internal_diagnostics_starlette_app_with_persistence_backing_denied_short_circuits() -> None:
    correlation_id = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    ledger = _SpyBillingEventsLedgerRepository()
    quarantine_repo = _SpyMismatchQuarantineRepository()
    recon_repo = _SpyReconciliationRunsRepository()
    deps = Adm02InternalDiagnosticsPersistenceBackedDependencies(
        identity=_IdentityStub(),
        billing_ledger_repository=ledger,
        mismatch_quarantine_repository=quarantine_repo,
        reconciliation_runs_repository=recon_repo,
        fact_of_access_appender=persisted,
        now_provider=lambda: expected_now,
        redaction=None,
        adm02_allowlisted_internal_admin_principal_ids=("p1",),
    )
    app = build_adm02_internal_diagnostics_starlette_app_with_persistence_backing(deps)

    async def main() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                ADM02_INTERNAL_DIAGNOSTICS_PATH,
                json={
                    "correlation_id": correlation_id,
                    "internal_admin_principal_id": "p-denied",
                    "internal_user_id": "u-target",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "denied"
        assert body["summary"] is None
        assert await persisted.recorded_for_tests() == ()
        assert ledger.summary_calls == 0
        assert quarantine_repo.summary_calls == 0
        assert recon_repo.summary_calls == 0

    _run(main())


def test_build_adm02_internal_diagnostics_starlette_app_with_persistence_backing_redaction_denied_short_circuits() -> (
    None
):
    correlation_id = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    ledger = _SpyBillingEventsLedgerRepository()
    quarantine_repo = _SpyMismatchQuarantineRepository()
    recon_repo = _SpyReconciliationRunsRepository()
    redaction = _RedactionCallsStub()
    deps = Adm02InternalDiagnosticsPersistenceBackedDependencies(
        identity=_IdentityStub(),
        billing_ledger_repository=ledger,
        mismatch_quarantine_repository=quarantine_repo,
        reconciliation_runs_repository=recon_repo,
        fact_of_access_appender=persisted,
        now_provider=lambda: expected_now,
        redaction=redaction,
        adm02_allowlisted_internal_admin_principal_ids=("p1",),
    )
    app = build_adm02_internal_diagnostics_starlette_app_with_persistence_backing(deps)

    async def main() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                ADM02_INTERNAL_DIAGNOSTICS_PATH,
                json={
                    "correlation_id": correlation_id,
                    "internal_admin_principal_id": "p-denied",
                    "internal_user_id": "u-target",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "denied"
        assert body["summary"] is None
        assert await persisted.recorded_for_tests() == ()
        assert redaction.calls == 0
        assert ledger.summary_calls == 0
        assert quarantine_repo.summary_calls == 0
        assert recon_repo.summary_calls == 0

    _run(main())


def test_build_adm02_internal_diagnostics_starlette_app_with_persistence_audit_denied_does_not_persist_record() -> None:
    correlation_id = new_correlation_id()
    expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    ledger = _SpyBillingEventsLedgerRepository()
    billing = Adm02BillingFactsLedgerReadAdapter(ledger)
    quarantine_repo = _SpyMismatchQuarantineRepository()
    quarantine = Adm02QuarantineMismatchReadAdapter(quarantine_repo)
    recon_repo = _SpyReconciliationRunsRepository()
    reconciliation = Adm02ReconciliationRunsReadAdapter(recon_repo)
    deps = Adm02InternalDiagnosticsPersistenceAuditDependencies(
        identity=_IdentityStub(),
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        fact_of_access_appender=persisted,
        now_provider=lambda: expected_now,
        redaction=None,
        adm02_allowlisted_internal_admin_principal_ids=("p1",),
    )
    app = build_adm02_internal_diagnostics_starlette_app_with_persistence_audit(deps)

    async def main() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                ADM02_INTERNAL_DIAGNOSTICS_PATH,
                json={
                    "correlation_id": correlation_id,
                    "internal_admin_principal_id": "p-denied",
                    "internal_user_id": "u-target",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "denied"
        assert body["summary"] is None

        recorded = await persisted.recorded_for_tests()
        assert recorded == ()
        assert ledger.summary_calls == 0
        assert quarantine_repo.summary_calls == 0
        assert recon_repo.summary_calls == 0

    _run(main())
