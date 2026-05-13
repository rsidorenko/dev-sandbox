"""ADM-02 diagnostics handler unit tests (fakes only; no network/DB)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace

from app.admin_support.adm02_diagnostics import ADM02_CAPABILITY_CLASS, Adm02DiagnosticsHandler
from app.admin_support.contracts import (
    Adm02BillingFactsCategory,
    Adm02BillingFactsDiagnostics,
    Adm02DiagnosticsInput,
    Adm02DiagnosticsOutcome,
    Adm02DiagnosticsSummary,
    Adm02FactOfAccessAuditRecord,
    Adm02FactOfAccessDisclosureCategory,
    Adm02QuarantineDiagnostics,
    Adm02QuarantineMarker,
    Adm02QuarantineReasonCode,
    Adm02ReconciliationDiagnostics,
    Adm02ReconciliationRunMarker,
    AdminActorRef,
    InternalUserTarget,
    RedactionMarker,
    TelegramUserTarget,
)
from app.shared.correlation import new_correlation_id

_FORBIDDEN_FRAGMENTS = (
    "external_event_id",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "database_url",
    "postgres://",
    "postgresql://",
    "bearer ",
    "private key",
    "raw_provider_payload",
    "traceback",
    "runtimeerror",
)


def _run(coro):
    return asyncio.run(coro)


class _AuthAllow:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed

    async def check_adm02_diagnostics_allowed(self, actor, *, correlation_id: str) -> bool:
        return self._allowed


class _Identity:
    def __init__(self, uid: str | None) -> None:
        self._uid = uid

    async def resolve_internal_user_id(self, target, *, correlation_id: str) -> str | None:
        return self._uid


class _Reads:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_billing_facts_diagnostics(self, internal_user_id: str) -> Adm02BillingFactsDiagnostics:
        self.calls.append("bill")

        return Adm02BillingFactsDiagnostics(
            category=Adm02BillingFactsCategory.HAS_ACCEPTED,
            internal_fact_refs=(f"ref-for-{internal_user_id}",),
        )

    async def get_quarantine_diagnostics(self, internal_user_id: str) -> Adm02QuarantineDiagnostics:
        self.calls.append("q")

        return Adm02QuarantineDiagnostics(
            marker=Adm02QuarantineMarker.NONE,
            reason_code=Adm02QuarantineReasonCode.NONE,
        )

    async def get_reconciliation_diagnostics(self, internal_user_id: str) -> Adm02ReconciliationDiagnostics:
        self.calls.append("rec")

        return Adm02ReconciliationDiagnostics(last_run_marker=Adm02ReconciliationRunMarker.NO_CHANGES)


class _ReadsSpy(_Reads):
    def __init__(self) -> None:
        super().__init__()

        self.any_calls = 0

    async def get_billing_facts_diagnostics(self, internal_user_id: str) -> Adm02BillingFactsDiagnostics:
        self.any_calls += 1

        return await super().get_billing_facts_diagnostics(internal_user_id)

    async def get_quarantine_diagnostics(self, internal_user_id: str) -> Adm02QuarantineDiagnostics:
        self.any_calls += 1

        return await super().get_quarantine_diagnostics(internal_user_id)

    async def get_reconciliation_diagnostics(self, internal_user_id: str) -> Adm02ReconciliationDiagnostics:
        self.any_calls += 1

        return await super().get_reconciliation_diagnostics(internal_user_id)


class _BillingRaise:
    async def get_billing_facts_diagnostics(self, internal_user_id: str) -> Adm02BillingFactsDiagnostics:
        raise RuntimeError("billing read failed")


class _BillingRaiseSensitive:
    async def get_billing_facts_diagnostics(self, internal_user_id: str) -> Adm02BillingFactsDiagnostics:
        raise RuntimeError(
            "traceback RuntimeError external_event_id provider_issuance_ref "
            "issue_idempotency_key DATABASE_URL postgres:// postgresql:// "
            "Bearer PRIVATE KEY raw_provider_payload"
        )


class _AuditOk:
    def __init__(self) -> None:
        self.records: list[Adm02FactOfAccessAuditRecord] = []

    async def append_fact_of_access(self, record: Adm02FactOfAccessAuditRecord) -> None:
        self.records.append(record)


class _AuditRaise:
    async def append_fact_of_access(self, record: Adm02FactOfAccessAuditRecord) -> None:
        raise RuntimeError("audit failed")


class _RedactionRaise:
    async def redact_diagnostics_summary(self, summary: Adm02DiagnosticsSummary) -> Adm02DiagnosticsSummary:
        raise RuntimeError("redaction failed")


class _ReadsWithSensitiveInternals(_Reads):
    def __init__(self) -> None:
        super().__init__()

        self._internal_sensitive_blob = (
            "external_event_id provider_issuance_ref issue_idempotency_key DATABASE_URL "
            "postgres:// postgresql:// Bearer PRIVATE KEY raw_provider_payload"
        )

    async def get_billing_facts_diagnostics(self, internal_user_id: str) -> Adm02BillingFactsDiagnostics:
        self.calls.append("bill")

        _ = self._internal_sensitive_blob

        return Adm02BillingFactsDiagnostics(
            category=Adm02BillingFactsCategory.HAS_ACCEPTED,
            internal_fact_refs=("opaque-fact-1", "opaque-fact-2"),
        )


def _handler(
    *,
    auth_allowed: bool = True,
    uid: str | None = "u-1",
    reads: _Reads | _ReadsSpy | None = None,
    billing: _Reads | _ReadsSpy | _BillingRaise | None = None,
    redaction=None,
    audit: _AuditOk | _AuditRaise | None = None,
) -> tuple[Adm02DiagnosticsHandler, _Reads | _ReadsSpy, _AuditOk | _AuditRaise]:
    r = reads or _Reads()

    a = audit if audit is not None else _AuditOk()

    bill = billing if billing is not None else r

    h = Adm02DiagnosticsHandler(
        authorization=_AuthAllow(auth_allowed),
        identity=_Identity(uid),
        billing=bill,
        quarantine=r,
        reconciliation=r,
        audit=a,
        redaction=redaction,
    )

    return h, r, a


def _inp(target, cid: str | None = None) -> Adm02DiagnosticsInput:
    return Adm02DiagnosticsInput(
        actor=AdminActorRef(internal_admin_principal_id="adm-1"),
        target=target,
        correlation_id=cid if cid is not None else new_correlation_id(),
    )


def test_adm02_success_redaction_before_audit_single_audit_call() -> None:
    async def main() -> None:
        reads = _Reads()

        audit = _AuditOk()

        class _RedSpy:
            def __init__(self) -> None:
                self.seen_marker_before: RedactionMarker | None = None

            async def redact_diagnostics_summary(self, summary: Adm02DiagnosticsSummary) -> Adm02DiagnosticsSummary:
                self.seen_marker_before = summary.redaction

                return replace(summary, redaction=RedactionMarker.PARTIAL)

        red = _RedSpy()

        h = Adm02DiagnosticsHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            billing=reads,
            quarantine=reads,
            reconciliation=reads,
            audit=audit,
            redaction=red,
        )

        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))

        assert r.outcome is Adm02DiagnosticsOutcome.SUCCESS

        assert r.summary is not None

        assert r.summary.redaction is RedactionMarker.PARTIAL

        assert red.seen_marker_before is RedactionMarker.NONE

        assert reads.calls == ["bill", "q", "rec"]

        assert len(audit.records) == 1

        rec = audit.records[0]

        assert rec.capability_class == ADM02_CAPABILITY_CLASS

        assert rec.internal_user_scope_ref == "u-1"

        assert rec.disclosure is Adm02FactOfAccessDisclosureCategory.PARTIAL

        assert rec.actor.internal_admin_principal_id == "adm-1"

    _run(main())


def test_adm02_denied_no_reads_no_audit() -> None:
    async def main() -> None:
        reads = _ReadsSpy()

        audit = _AuditOk()

        h, _, _ = _handler(auth_allowed=False, reads=reads, audit=audit)

        r = await h.handle(_inp(TelegramUserTarget(telegram_user_id=42)))

        assert r.outcome is Adm02DiagnosticsOutcome.DENIED

        assert r.summary is None

        assert reads.any_calls == 0

        assert audit.records == []

    _run(main())


def test_adm02_target_not_resolved_no_reads_no_audit() -> None:
    async def main() -> None:
        reads = _ReadsSpy()

        audit = _AuditOk()

        h, _, _ = _handler(uid=None, reads=reads, audit=audit)

        r = await h.handle(_inp(TelegramUserTarget(telegram_user_id=99)))

        assert r.outcome is Adm02DiagnosticsOutcome.TARGET_NOT_RESOLVED

        assert reads.any_calls == 0

        assert audit.records == []

    _run(main())


def test_adm02_invalid_correlation() -> None:
    async def main() -> None:
        h, reads, audit = _handler()

        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1"), cid="not-valid"))

        assert r.outcome is Adm02DiagnosticsOutcome.INVALID_INPUT

        assert r.summary is None

        assert reads.calls == []

        assert audit.records == []

    _run(main())


def test_adm02_read_port_dependency_failure() -> None:
    async def main() -> None:
        reads = _ReadsSpy()

        audit = _AuditOk()

        h = Adm02DiagnosticsHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            billing=_BillingRaise(),
            quarantine=reads,
            reconciliation=reads,
            audit=audit,
        )

        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))

        assert r.outcome is Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE

        assert r.summary is None

        assert reads.any_calls == 0

        assert audit.records == []

    _run(main())


def test_adm02_audit_dependency_failure() -> None:
    async def main() -> None:
        reads = _Reads()

        h, _, _ = _handler(reads=reads, audit=_AuditRaise())

        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))

        assert r.outcome is Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE

        assert r.summary is None

        assert reads.calls == ["bill", "q", "rec"]

    _run(main())


def test_adm02_redaction_exception_dependency_failure() -> None:
    async def main() -> None:
        reads = _Reads()

        audit = _AuditOk()

        h = Adm02DiagnosticsHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            billing=reads,
            quarantine=reads,
            reconciliation=reads,
            audit=audit,
            redaction=_RedactionRaise(),
        )

        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))

        assert r.outcome is Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE

        assert r.summary is None

        assert reads.calls == ["bill", "q", "rec"]

        assert audit.records == []

    _run(main())


def test_adm02_success_without_redaction_audit_unredacted() -> None:
    async def main() -> None:
        h, reads, audit = _handler()

        cid = new_correlation_id()

        r = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1"), cid=cid))

        assert r.outcome is Adm02DiagnosticsOutcome.SUCCESS

        assert r.summary is not None

        assert r.summary.redaction is RedactionMarker.NONE

        assert reads.calls == ["bill", "q", "rec"]

        assert len(audit.records) == 1

        assert audit.records[0].disclosure is Adm02FactOfAccessDisclosureCategory.UNREDACTED

        assert audit.records[0].correlation_id == cid

    _run(main())


def test_adm02_success_leak_guard_sensitive_read_internals_not_exposed_and_audit_bounded() -> None:
    async def main() -> None:
        reads = _ReadsWithSensitiveInternals()

        audit = _AuditOk()

        h, _, _ = _handler(reads=reads, audit=audit)

        cid = new_correlation_id()

        result = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1"), cid=cid))

        encoded_result = json.dumps(asdict(result), sort_keys=True).lower()

        assert result.outcome is Adm02DiagnosticsOutcome.SUCCESS

        assert result.summary is not None

        assert result.summary.billing.internal_fact_refs == ("opaque-fact-1", "opaque-fact-2")

        assert result.summary.redaction in {
            RedactionMarker.NONE,
            RedactionMarker.PARTIAL,
            RedactionMarker.FULL,
        }

        assert "internal_fact_refs" in encoded_result

        for forbidden in _FORBIDDEN_FRAGMENTS:
            assert forbidden not in encoded_result

        assert len(audit.records) == 1

        audit_record = audit.records[0]

        assert audit_record.disclosure in {
            Adm02FactOfAccessDisclosureCategory.UNREDACTED,
            Adm02FactOfAccessDisclosureCategory.PARTIAL,
            Adm02FactOfAccessDisclosureCategory.FULLY_REDACTED,
        }

        encoded_audit = json.dumps(asdict(audit_record), sort_keys=True).lower()

        for forbidden in _FORBIDDEN_FRAGMENTS:
            assert forbidden not in encoded_audit

    _run(main())


def test_adm02_read_port_sensitive_exception_fail_closed_no_leak() -> None:
    async def main() -> None:
        reads = _ReadsSpy()

        audit = _AuditOk()

        h = Adm02DiagnosticsHandler(
            authorization=_AuthAllow(True),
            identity=_Identity("u-1"),
            billing=_BillingRaiseSensitive(),
            quarantine=reads,
            reconciliation=reads,
            audit=audit,
        )

        result = await h.handle(_inp(InternalUserTarget(internal_user_id="u-1")))

        encoded_result = json.dumps(asdict(result), sort_keys=True).lower()

        assert result.outcome is Adm02DiagnosticsOutcome.DEPENDENCY_FAILURE

        assert result.summary is None

        assert reads.any_calls == 0

        assert audit.records == []

        for forbidden in _FORBIDDEN_FRAGMENTS:
            assert forbidden not in encoded_result

    _run(main())
