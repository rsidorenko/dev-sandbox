"""Boundary surface: ADM-01 contracts import and construction (no I/O)."""

from app.admin_support import (
    Adm01LookupInput,
    Adm01LookupSummary,
    Adm01SubscriptionStatusSummary,
    Adm01SupportAccessReadinessBucket,
    Adm01SupportNextAction,
    Adm01SupportReadinessSummary,
    Adm01SupportSubscriptionBucket,
    Adm02EnsureAccessAuditEvent,
    Adm02EnsureAccessAuditEventType,
    Adm02EnsureAccessAuditLookupOutcome,
    Adm02EnsureAccessAuditLookupResponse,
    Adm02EnsureAccessAuditOutcomeBucket,
    Adm02EnsureAccessAuditPort,
    Adm02EnsureAccessAuditPrincipalMarker,
    Adm02EnsureAccessAuditReadResult,
    Adm02EnsureAccessInput,
    Adm02EnsureAccessOutcome,
    Adm02EnsureAccessRemediationResult,
    Adm02EnsureAccessResult,
    Adm02EnsureAccessSummary,
    AdminActorRef,
    AdminPolicyFlag,
    EntitlementSummary,
    EntitlementSummaryCategory,
    FanoutAdm02EnsureAccessAuditSink,
    InternalUserTarget,
    IssuanceOperationalState,
    IssuanceOperationalSummary,
    PostgresAdm02EnsureAccessAuditSink,
    RedactionMarker,
    StructuredLoggingAdm02EnsureAccessAuditSink,
)
from app.shared.correlation import new_correlation_id


def test_adm01_contracts_construct() -> None:
    inp = Adm01LookupInput(
        actor=AdminActorRef(internal_admin_principal_id="adm-x"),
        target=InternalUserTarget(internal_user_id="user-y"),
        correlation_id=new_correlation_id(),
    )
    out = Adm01LookupSummary(
        subscription=Adm01SubscriptionStatusSummary(snapshot=None),
        entitlement=EntitlementSummary(category=EntitlementSummaryCategory.UNKNOWN),
        policy_flag=AdminPolicyFlag.DEFAULT,
        issuance=IssuanceOperationalSummary(state=IssuanceOperationalState.UNKNOWN),
        support_readiness=Adm01SupportReadinessSummary(
            telegram_identity_known=False,
            subscription_bucket=Adm01SupportSubscriptionBucket.UNKNOWN,
            access_readiness_bucket=Adm01SupportAccessReadinessBucket.NOT_APPLICABLE_NO_ACTIVE_SUBSCRIPTION,
            recommended_next_action=Adm01SupportNextAction.ASK_USER_TO_USE_STATUS,
        ),
        redaction=RedactionMarker.NONE,
    )
    assert inp.target.internal_user_id == "user-y"
    assert out.redaction is RedactionMarker.NONE


def test_adm02_ensure_access_contracts_construct() -> None:
    inp = Adm02EnsureAccessInput(
        actor=AdminActorRef(internal_admin_principal_id="adm-y"),
        target=InternalUserTarget(internal_user_id="user-z"),
        correlation_id=new_correlation_id(),
    )
    out = Adm02EnsureAccessResult(
        outcome=Adm02EnsureAccessOutcome.SUCCESS,
        correlation_id=inp.correlation_id,
        summary=Adm02EnsureAccessSummary(
            telegram_identity_known=True,
            subscription_bucket=Adm01SupportSubscriptionBucket.ACTIVE,
            access_readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
            remediation_result=Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
            recommended_next_action=Adm01SupportNextAction.ASK_USER_TO_USE_GET_ACCESS,
        ),
    )
    assert inp.target.internal_user_id == "user-z"
    assert out.summary is not None
    assert out.summary.remediation_result is Adm02EnsureAccessRemediationResult.ISSUED_ACCESS


def test_adm02_ensure_access_audit_contracts_construct() -> None:
    cid = new_correlation_id()
    event = Adm02EnsureAccessAuditEvent(
        event_type=Adm02EnsureAccessAuditEventType.ENSURE_ACCESS,
        outcome_bucket=Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS,
        remediation_result=Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
        readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
        principal_marker=Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED,
        correlation_id=cid,
    )
    assert event.event_type is Adm02EnsureAccessAuditEventType.ENSURE_ACCESS
    assert event.outcome_bucket is Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS
    assert event.principal_marker is Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED


def test_adm02_ensure_access_audit_read_contracts_construct() -> None:
    result = Adm02EnsureAccessAuditReadResult(items=())
    response = Adm02EnsureAccessAuditLookupResponse(
        outcome=Adm02EnsureAccessAuditLookupOutcome.SUCCESS,
        correlation_id=new_correlation_id(),
        result=result,
    )
    assert response.result is result


def test_adm02_ensure_access_structured_logging_sink_is_exported_audit_port() -> None:
    sink = StructuredLoggingAdm02EnsureAccessAuditSink()
    _ = Adm02EnsureAccessAuditPort
    assert hasattr(sink, "append_ensure_access_event")


def test_adm02_ensure_access_fanout_and_postgres_sinks_are_exported() -> None:
    _ = Adm02EnsureAccessAuditPort
    assert hasattr(FanoutAdm02EnsureAccessAuditSink, "append_ensure_access_event")
    assert hasattr(PostgresAdm02EnsureAccessAuditSink, "append_ensure_access_event")
