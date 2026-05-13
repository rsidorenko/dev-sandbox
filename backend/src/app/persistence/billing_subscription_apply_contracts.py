"""UC-05 apply idempotency + append-only apply audit (contracts only)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BillingSubscriptionApplyOutcome(StrEnum):
    """Outcome stored in billing_subscription_apply_records (durable idempotency)."""

    ACTIVE_APPLIED = "active_applied"
    NO_ACTIVATION = "no_activation"
    NEEDS_REVIEW = "needs_review"


class BillingSubscriptionApplyReason(StrEnum):
    """Allowlisted reason codes for apply audit (low-cardinality, no free text)."""

    OK = "ok"
    LEDGER_STATUS_NOT_ACCEPTED = "ledger_status_not_accepted"
    UNKNOWN_EVENT_TYPE = "unknown_event_type"
    MISSING_INTERNAL_USER = "missing_internal_user"
    NO_STATE_CHANGE = "no_state_change"


@dataclass(frozen=True, slots=True)
class BillingSubscriptionApplyAuditRecord:
    """Append-only UC-05 apply audit row (normalized fields only; no raw payload)."""

    internal_fact_ref: str
    internal_user_id: str | None
    billing_provider_key: str
    external_event_id: str
    event_type: str
    billing_event_status: str
    apply_outcome: BillingSubscriptionApplyOutcome
    reason: BillingSubscriptionApplyReason
