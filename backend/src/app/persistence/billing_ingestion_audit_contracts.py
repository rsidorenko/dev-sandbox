"""UC-04 internal billing ingestion audit (append-only; no raw provider payload)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

BILLING_INGESTION_AUDIT_OPERATION = "billing_fact_ingested"
BILLING_INGESTION_OUTCOME_ACCEPTED = "accepted"
BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY = "idempotent_replay"


@dataclass(frozen=True, slots=True)
class BillingIngestionAuditRecord:
    """Fields persisted after successful billing_events_ledger write (canonical stored record)."""

    internal_fact_ref: str
    billing_provider_key: str
    external_event_id: str
    ingestion_correlation_id: str
    operation: str
    outcome: str
    billing_event_status: str
    is_idempotent_replay: bool


class BillingIngestionAuditAppender(Protocol):
    async def append(self, record: BillingIngestionAuditRecord) -> None: ...


class InMemoryBillingIngestionAuditAppender:
    """Test double: append-only list with readback (tests only)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: list[BillingIngestionAuditRecord] = []

    async def append(self, record: BillingIngestionAuditRecord) -> None:
        async with self._lock:
            self._records.append(record)

    async def records_for_tests(self) -> tuple[BillingIngestionAuditRecord, ...]:
        async with self._lock:
            return tuple(self._records)
