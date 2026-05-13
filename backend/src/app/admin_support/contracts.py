"""ADM-01 (UC-09) and ADM-02 boundary contracts: read-only admin types and read ports.

No transport, persistence implementation, or RBAC — types and Protocols; orchestration in `adm01_lookup` / `adm02_diagnostics`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.application.interfaces import SubscriptionSnapshot


@dataclass(frozen=True, slots=True)
class AdminActorRef:
    """Authenticated admin principal reference (opaque id; validated at ingress)."""

    internal_admin_principal_id: str


@dataclass(frozen=True, slots=True)
class InternalAdminPrincipalExtractionInput:
    """Trusted-boundary extraction input (transport-agnostic primitives only)."""

    principal_id_candidate: str | None
    trusted_source: bool


class InternalAdminPrincipalExtractionOutcome(StrEnum):
    """Fail-closed principal extraction outcome for internal admin ingress."""

    SUCCESS = "success"
    MISSING_PRINCIPAL = "missing_principal"
    MALFORMED_PRINCIPAL = "malformed_principal"
    UNTRUSTED_SOURCE = "untrusted_source"


@dataclass(frozen=True, slots=True)
class InternalAdminPrincipalExtractionResult:
    """Extraction result: principal is present only on SUCCESS."""

    outcome: InternalAdminPrincipalExtractionOutcome
    principal: AdminActorRef | None


class InternalAdminPrincipalExtractor(Protocol):
    """Extract normalized internal admin principal from trusted ingress boundary."""

    async def extract_trusted_internal_admin_principal(
        self,
        inp: InternalAdminPrincipalExtractionInput,
    ) -> InternalAdminPrincipalExtractionResult: ...


@dataclass(frozen=True, slots=True)
class InternalUserTarget:
    """Allowlisted lookup by internal user id."""

    internal_user_id: str


@dataclass(frozen=True, slots=True)
class TelegramUserTarget:
    """Allowlisted lookup by Telegram user id (no free-form identifiers)."""

    telegram_user_id: int


AdminTargetLookup = InternalUserTarget | TelegramUserTarget


@dataclass(frozen=True, slots=True)
class Adm01LookupInput:
    """Normalized ADM-01 lookup input after ingress validation."""

    actor: AdminActorRef
    target: AdminTargetLookup
    correlation_id: str


class EntitlementSummaryCategory(StrEnum):
    UNKNOWN = "unknown"
    NONE = "none"
    INACTIVE = "inactive"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class EntitlementSummary:
    category: EntitlementSummaryCategory


class AdminPolicyFlag(StrEnum):
    """Low-cardinality policy hint for admin summary (no secrets)."""

    UNKNOWN = "unknown"
    DEFAULT = "default"
    ENFORCE_MANUAL_REVIEW = "enforce_manual_review"


class Adm01SupportSubscriptionBucket(StrEnum):
    UNKNOWN = "unknown"
    INACTIVE = "inactive"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Adm01SupportAccessReadinessBucket(StrEnum):
    NOT_APPLICABLE_NO_ACTIVE_SUBSCRIPTION = "not_applicable_no_active_subscription"
    ACTIVE_ACCESS_NOT_READY = "active_access_not_ready"
    ACTIVE_ACCESS_READY = "active_access_ready"
    UNKNOWN_DUE_TO_INTERNAL_ERROR = "unknown_due_to_internal_error"


class Adm01SupportNextAction(StrEnum):
    ASK_USER_TO_USE_STATUS = "ask_user_to_use_status"
    ASK_USER_TO_USE_GET_ACCESS = "ask_user_to_use_get_access"
    INVESTIGATE_BILLING_APPLY = "investigate_billing_apply"
    INVESTIGATE_ISSUANCE = "investigate_issuance"


@dataclass(frozen=True, slots=True)
class Adm01SupportReadinessSummary:
    telegram_identity_known: bool
    subscription_bucket: Adm01SupportSubscriptionBucket
    access_readiness_bucket: Adm01SupportAccessReadinessBucket
    recommended_next_action: Adm01SupportNextAction


class IssuanceOperationalState(StrEnum):
    UNKNOWN = "unknown"
    NONE = "none"
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IssuanceOperationalSummary:
    state: IssuanceOperationalState


class RedactionMarker(StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class Adm01SubscriptionStatusSummary:
    """Reuses slice-1 subscription snapshot shape; None if unknown or not resolved."""

    snapshot: SubscriptionSnapshot | None


@dataclass(frozen=True, slots=True)
class Adm01LookupSummary:
    subscription: Adm01SubscriptionStatusSummary
    entitlement: EntitlementSummary
    policy_flag: AdminPolicyFlag
    issuance: IssuanceOperationalSummary
    support_readiness: Adm01SupportReadinessSummary
    redaction: RedactionMarker


class Adm01IdentityResolvePort(Protocol):
    """Map allowlisted targets to internal user id (fail-closed)."""

    async def resolve_internal_user_id(
        self,
        target: AdminTargetLookup,
        *,
        correlation_id: str,
    ) -> str | None: ...


class Adm01SubscriptionReadPort(Protocol):
    """Read-only subscription snapshot access (aligned with SubscriptionSnapshotReader)."""

    async def get_subscription_snapshot(self, internal_user_id: str) -> SubscriptionSnapshot | None: ...


class Adm01EntitlementReadPort(Protocol):
    async def get_entitlement_summary(self, internal_user_id: str) -> EntitlementSummary: ...


class Adm01IssuanceReadPort(Protocol):
    async def get_issuance_summary(self, internal_user_id: str) -> IssuanceOperationalSummary: ...


class Adm01PolicyReadPort(Protocol):
    """Read-only low-cardinality policy hint for admin summary (no RBAC or persistence details)."""

    async def get_policy_flag(self, internal_user_id: str) -> AdminPolicyFlag: ...


class Adm01LookupOutcome(StrEnum):
    """Normalized ADM-01 orchestration outcome (transport maps later; no secrets)."""

    SUCCESS = "success"
    DENIED = "denied"
    TARGET_NOT_RESOLVED = "target_not_resolved"
    INVALID_INPUT = "invalid_input"
    DEPENDENCY_FAILURE = "dependency_failure"


@dataclass(frozen=True, slots=True)
class Adm01LookupResult:
    """Handler result: summary only on SUCCESS."""

    outcome: Adm01LookupOutcome
    correlation_id: str
    summary: Adm01LookupSummary | None


class Adm01AuthorizationPort(Protocol):
    """Narrow capability gate for ADM-01 read lookup (fail-closed at handler)."""

    async def check_adm01_lookup_allowed(
        self,
        actor: AdminActorRef,
        *,
        correlation_id: str,
    ) -> bool: ...


class Adm01RedactionPort(Protocol):
    """Optional boundary redaction for assembled lookup summary."""

    async def redact_lookup_summary(self, summary: Adm01LookupSummary) -> Adm01LookupSummary: ...


# --- ADM-02: billing / quarantine / reconciliation diagnostics (read-only; higher sensitivity than ADM-01) ---


@dataclass(frozen=True, slots=True)
class Adm02DiagnosticsInput:
    actor: AdminActorRef
    target: AdminTargetLookup
    correlation_id: str


class Adm02BillingFactsCategory(StrEnum):
    UNKNOWN = "unknown"
    NONE = "none"
    HAS_ACCEPTED = "has_accepted"


@dataclass(frozen=True, slots=True)
class Adm02BillingFactsDiagnostics:
    """Opaque internal fact refs only; bounded cardinality expected from port implementations."""

    category: Adm02BillingFactsCategory
    internal_fact_refs: tuple[str, ...]


class Adm02QuarantineMarker(StrEnum):
    UNKNOWN = "unknown"
    NONE = "none"
    ACTIVE = "active"


class Adm02QuarantineReasonCode(StrEnum):
    UNKNOWN = "unknown"
    NONE = "none"
    MISMATCH = "mismatch"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True)
class Adm02QuarantineDiagnostics:
    marker: Adm02QuarantineMarker
    reason_code: Adm02QuarantineReasonCode


class Adm02ReconciliationRunMarker(StrEnum):
    UNKNOWN = "unknown"
    NONE = "none"
    NO_CHANGES = "no_changes"
    FACTS_DISCOVERED = "facts_discovered"


@dataclass(frozen=True, slots=True)
class Adm02ReconciliationDiagnostics:
    last_run_marker: Adm02ReconciliationRunMarker


@dataclass(frozen=True, slots=True)
class Adm02DiagnosticsSummary:
    billing: Adm02BillingFactsDiagnostics
    quarantine: Adm02QuarantineDiagnostics
    reconciliation: Adm02ReconciliationDiagnostics
    redaction: RedactionMarker


class Adm02DiagnosticsOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    TARGET_NOT_RESOLVED = "target_not_resolved"
    INVALID_INPUT = "invalid_input"
    DEPENDENCY_FAILURE = "dependency_failure"


@dataclass(frozen=True, slots=True)
class Adm02DiagnosticsResult:
    outcome: Adm02DiagnosticsOutcome
    correlation_id: str
    summary: Adm02DiagnosticsSummary | None


class Adm02FactOfAccessDisclosureCategory(StrEnum):
    """Normalized disclosure outcome for append-only fact-of-access audit (no response payload)."""

    UNREDACTED = "unredacted"
    PARTIAL = "partial"
    FULLY_REDACTED = "fully_redacted"


@dataclass(frozen=True, slots=True)
class Adm02FactOfAccessAuditRecord:
    actor: AdminActorRef
    capability_class: str
    internal_user_scope_ref: str
    correlation_id: str
    disclosure: Adm02FactOfAccessDisclosureCategory


class Adm02AuthorizationPort(Protocol):
    async def check_adm02_diagnostics_allowed(
        self,
        actor: AdminActorRef,
        *,
        correlation_id: str,
    ) -> bool: ...


class Adm02BillingFactsReadPort(Protocol):
    async def get_billing_facts_diagnostics(self, internal_user_id: str) -> Adm02BillingFactsDiagnostics: ...


class Adm02QuarantineReadPort(Protocol):
    async def get_quarantine_diagnostics(self, internal_user_id: str) -> Adm02QuarantineDiagnostics: ...


class Adm02ReconciliationReadPort(Protocol):
    async def get_reconciliation_diagnostics(self, internal_user_id: str) -> Adm02ReconciliationDiagnostics: ...


class Adm02RedactionPort(Protocol):
    async def redact_diagnostics_summary(self, summary: Adm02DiagnosticsSummary) -> Adm02DiagnosticsSummary: ...


class Adm02FactOfAccessAuditPort(Protocol):
    async def append_fact_of_access(self, record: Adm02FactOfAccessAuditRecord) -> None: ...


class Adm02EnsureAccessOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    INVALID_INPUT = "invalid_input"
    DEPENDENCY_FAILURE = "dependency_failure"


class Adm02EnsureAccessRemediationResult(StrEnum):
    NOOP_IDENTITY_UNKNOWN = "noop_identity_unknown"
    NOOP_NO_ACTIVE_SUBSCRIPTION = "noop_no_active_subscription"
    NOOP_ACCESS_ALREADY_READY = "noop_access_already_ready"
    ISSUED_ACCESS = "issued_access"
    FAILED_SAFE = "failed_safe"


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessSummary:
    telegram_identity_known: bool
    subscription_bucket: Adm01SupportSubscriptionBucket
    access_readiness_bucket: Adm01SupportAccessReadinessBucket
    remediation_result: Adm02EnsureAccessRemediationResult
    recommended_next_action: Adm01SupportNextAction


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessInput:
    actor: AdminActorRef
    target: AdminTargetLookup
    correlation_id: str


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessResult:
    outcome: Adm02EnsureAccessOutcome
    correlation_id: str
    summary: Adm02EnsureAccessSummary | None


class Adm02EnsureAccessAuthorizationPort(Protocol):
    async def check_adm02_ensure_access_allowed(
        self,
        actor: AdminActorRef,
        *,
        correlation_id: str,
    ) -> bool: ...


class Adm02MutationOptInPort(Protocol):
    async def check_adm02_mutation_opt_in_enabled(self, *, correlation_id: str) -> bool: ...


class Adm02EnsureAccessMutationPort(Protocol):
    async def ensure_access_issued(self, internal_user_id: str, *, correlation_id: str) -> bool:
        """Return True iff a new issuance was created; False if already ready/idempotent."""
        ...


class Adm02EnsureAccessAuditEventType(StrEnum):
    ENSURE_ACCESS = "ensure_access"


class Adm02EnsureAccessAuditOutcomeBucket(StrEnum):
    DENIED_UNAUTHORIZED = "denied_unauthorized"
    DENIED_MUTATION_OPT_IN_DISABLED = "denied_mutation_opt_in_disabled"
    NOOP_IDENTITY_UNKNOWN = "noop_identity_unknown"
    NOOP_NO_ACTIVE_SUBSCRIPTION = "noop_no_active_subscription"
    NOOP_ACCESS_ALREADY_READY = "noop_access_already_ready"
    ISSUED_ACCESS = "issued_access"
    FAILED_SAFE = "failed_safe"
    DEPENDENCY_FAILURE = "dependency_failure"
    INVALID_INPUT = "invalid_input"


class Adm02EnsureAccessAuditPrincipalMarker(StrEnum):
    INTERNAL_ADMIN_REDACTED = "internal_admin_redacted"


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessAuditEvent:
    event_type: Adm02EnsureAccessAuditEventType
    outcome_bucket: Adm02EnsureAccessAuditOutcomeBucket
    remediation_result: Adm02EnsureAccessRemediationResult | None
    readiness_bucket: Adm01SupportAccessReadinessBucket | None
    principal_marker: Adm02EnsureAccessAuditPrincipalMarker
    correlation_id: str


class Adm02EnsureAccessAuditPort(Protocol):
    async def append_ensure_access_event(self, event: Adm02EnsureAccessAuditEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessAuditReadQuery:
    correlation_id: str | None
    limit: int


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessAuditEvidenceItem:
    created_at: str
    event_type: Adm02EnsureAccessAuditEventType
    outcome_bucket: Adm02EnsureAccessAuditOutcomeBucket
    remediation_result: Adm02EnsureAccessRemediationResult | None
    readiness_bucket: Adm01SupportAccessReadinessBucket | None
    principal_marker: Adm02EnsureAccessAuditPrincipalMarker
    correlation_id: str
    source_marker: str | None


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessAuditReadResult:
    items: tuple[Adm02EnsureAccessAuditEvidenceItem, ...]


class Adm02EnsureAccessAuditReadPort(Protocol):
    async def read_ensure_access_audit_evidence(
        self,
        query: Adm02EnsureAccessAuditReadQuery,
    ) -> Adm02EnsureAccessAuditReadResult: ...


class Adm02EnsureAccessAuditLookupOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    INVALID_INPUT = "invalid_input"
    DEPENDENCY_FAILURE = "dependency_failure"


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessAuditLookupInput:
    actor: AdminActorRef
    correlation_id: str
    evidence_correlation_id: str | None
    limit: int


@dataclass(frozen=True, slots=True)
class Adm02EnsureAccessAuditLookupResponse:
    outcome: Adm02EnsureAccessAuditLookupOutcome
    correlation_id: str
    result: Adm02EnsureAccessAuditReadResult | None
