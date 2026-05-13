"""Contracts for the first billing_events_ledger source-of-truth slice.

Append-only accepted billing facts + minimal per-user diagnostics summary.
No storage implementation, no DB/SQL, no runtime wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BillingEventAmountCurrency:
    """Minimal normalized amount/currency shape for a billing event.

    Amount is expressed in minor units (for example, cents) to keep it storage-agnostic.
    For non-monetary events amount may be None; currency_code may still be set for consistency.
    """

    amount_minor_units: int | None
    currency_code: str | None


class BillingEventLedgerStatus(StrEnum):
    """Minimal status of a ledger record from ingestion point of view.

    Append-only semantics: once accepted, a record is not updated or deleted.
    Duplicate/ignored represent idempotent outcomes for the same external_event_id.
    """

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class BillingEventLedgerRecord:
    """Append-only accepted billing fact stored in billing_events_ledger.

    This record captures only normalized evidence:
    - internal_fact_ref (billing_event_id) for internal linking and diagnostics;
    - provider identifiers (provider_key, external_event_id);
    - normalized event type and timing markers;
    - optional internal linkage (user, checkout attempt);
    - minimal normalized amount/currency;
    - ingestion status and correlation id.

    It intentionally does not contain raw payloads, free-form text or provider-specific blobs.
    """

    internal_fact_ref: str
    billing_provider_key: str
    external_event_id: str
    event_type: str
    event_effective_at: datetime
    event_received_at: datetime
    internal_user_id: str | None
    checkout_attempt_id: str | None
    amount_currency: BillingEventAmountCurrency | None
    status: BillingEventLedgerStatus
    ingestion_correlation_id: str


class BillingFactsPresenceCategory(StrEnum):
    """Presence category for per-user billing facts diagnostics summary."""

    UNKNOWN = "unknown"
    NONE = "none"
    HAS_ACCEPTED = "has_accepted"


@dataclass(frozen=True, slots=True)
class BillingEventsLedgerUserSummary:
    """Minimal per-user billing facts diagnostics summary.

    This shape is intended as a future backing source for Adm02BillingFactsReadPort,
    but remains persistence-local and adapter-agnostic.
    Cardinality of internal_fact_refs is expected to be bounded by implementations.
    """

    category: BillingFactsPresenceCategory
    internal_fact_refs: tuple[str, ...]


class BillingEventsLedgerRepository(Protocol):
    """Append-only persistence contract for billing_events_ledger.

    Implementations must be append-only: no update/delete/upsert surfaces beyond
    the idempotent append_or_get_by_provider_and_external_id operation.
    """

    async def append_or_get_by_provider_and_external_id(
        self,
        record: BillingEventLedgerRecord,
    ) -> BillingEventLedgerRecord:
        """Append a new accepted billing fact or return existing one for the same provider/event id.

        Idempotent with respect to (billing_provider_key, external_event_id):
        - first call persists the record and returns the stored representation;
        - subsequent calls return the existing stored record without creating duplicates.
        """

    async def get_user_billing_facts_summary(
        self,
        internal_user_id: str,
    ) -> BillingEventsLedgerUserSummary:
        """Return a bounded diagnostics summary of accepted billing facts for a user."""

    async def get_by_internal_fact_ref(
        self,
        internal_fact_ref: str,
    ) -> BillingEventLedgerRecord | None:
        """Return the ledger row for the primary key, or None if not found."""
