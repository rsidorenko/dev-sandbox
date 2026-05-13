"""Persistence implementations (slice 1: in-memory adapters for tests and local composition)."""

from __future__ import annotations

from typing import Any

from app.persistence.billing_events_ledger_contracts import (
    BillingEventAmountCurrency,
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
    BillingEventsLedgerRepository,
    BillingEventsLedgerUserSummary,
    BillingFactsPresenceCategory,
)
from app.persistence.billing_events_ledger_in_memory import InMemoryBillingEventsLedgerRepository
from app.persistence.billing_ingestion_audit_contracts import (
    BILLING_INGESTION_AUDIT_OPERATION,
    BILLING_INGESTION_OUTCOME_ACCEPTED,
    BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY,
    BillingIngestionAuditAppender,
    BillingIngestionAuditRecord,
    InMemoryBillingIngestionAuditAppender,
)
from app.persistence.in_memory import (
    InMemoryAuditAppender,
    InMemoryIdempotencyRepository,
    InMemorySubscriptionSnapshotReader,
    InMemoryUserIdentityRepository,
)
from app.persistence.issuance_state_record import IssuanceStatePersistence, IssuanceStateRow
from app.persistence.mismatch_quarantine_contracts import (
    MismatchQuarantineReasonCode,
    MismatchQuarantineRecord,
    MismatchQuarantineRepository,
    MismatchQuarantineResolutionStatus,
    MismatchQuarantineSourceType,
    MismatchQuarantineSummaryMarker,
    MismatchQuarantineUserSummary,
)
from app.persistence.mismatch_quarantine_in_memory import InMemoryMismatchQuarantineRepository
from app.persistence.postgres_audit import PostgresAuditAppender
from app.persistence.postgres_billing_events_ledger import PostgresBillingEventsLedgerRepository
from app.persistence.postgres_billing_ingestion_audit import PostgresBillingIngestionAuditAppender
from app.persistence.postgres_idempotency import PostgresIdempotencyRepository
from app.persistence.postgres_issuance_state import PostgresIssuanceStateRepository
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_telegram_update_dedup import PostgresTelegramUpdateDedupGuard
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository
from app.persistence.reconciliation_runs_contracts import (
    ReconciliationRunOutcome,
    ReconciliationRunRecord,
    ReconciliationRunsRepository,
    ReconciliationRunStatus,
    ReconciliationRunUserSummary,
)
from app.persistence.reconciliation_runs_in_memory import InMemoryReconciliationRunsRepository


def __getattr__(name: str) -> Any:
    """Lazy ADM-02 fact-of-access exports to avoid import cycles with admin_support package init."""

    if name in (
        "Adm02FactOfAccessAppendRecord",
        "Adm02FactOfAccessRecordAppender",
        "InMemoryAdm02FactOfAccessRecordAppender",
    ):
        from app.persistence import adm02_fact_of_access as _adm02

        return getattr(_adm02, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BILLING_INGESTION_AUDIT_OPERATION",
    "BILLING_INGESTION_OUTCOME_ACCEPTED",
    "BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY",
    "Adm02FactOfAccessAppendRecord",
    "Adm02FactOfAccessRecordAppender",
    "BillingEventAmountCurrency",
    "BillingEventLedgerRecord",
    "BillingEventLedgerStatus",
    "BillingEventsLedgerRepository",
    "BillingEventsLedgerUserSummary",
    "BillingFactsPresenceCategory",
    "BillingIngestionAuditAppender",
    "BillingIngestionAuditRecord",
    "InMemoryAdm02FactOfAccessRecordAppender",
    "InMemoryAuditAppender",
    "InMemoryBillingEventsLedgerRepository",
    "InMemoryBillingIngestionAuditAppender",
    "InMemoryIdempotencyRepository",
    "InMemoryMismatchQuarantineRepository",
    "InMemoryReconciliationRunsRepository",
    "InMemorySubscriptionSnapshotReader",
    "InMemoryUserIdentityRepository",
    "IssuanceStatePersistence",
    "IssuanceStateRow",
    "MismatchQuarantineReasonCode",
    "MismatchQuarantineRecord",
    "MismatchQuarantineRepository",
    "MismatchQuarantineResolutionStatus",
    "MismatchQuarantineSourceType",
    "MismatchQuarantineSummaryMarker",
    "MismatchQuarantineUserSummary",
    "PostgresAuditAppender",
    "PostgresBillingEventsLedgerRepository",
    "PostgresBillingIngestionAuditAppender",
    "PostgresIdempotencyRepository",
    "PostgresIssuanceStateRepository",
    "PostgresSubscriptionSnapshotReader",
    "PostgresTelegramUpdateDedupGuard",
    "PostgresUserIdentityRepository",
    "ReconciliationRunOutcome",
    "ReconciliationRunRecord",
    "ReconciliationRunStatus",
    "ReconciliationRunUserSummary",
    "ReconciliationRunsRepository",
]
