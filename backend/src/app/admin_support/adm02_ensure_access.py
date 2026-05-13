"""ADM-02 ensure-access remediation orchestration (internal-only; no transport/storage here)."""

from __future__ import annotations

from app.admin_support.contracts import (
    Adm01IdentityResolvePort,
    Adm01IssuanceReadPort,
    Adm01SubscriptionReadPort,
    Adm01SupportAccessReadinessBucket,
    Adm01SupportNextAction,
    Adm01SupportSubscriptionBucket,
    Adm02EnsureAccessAuditEvent,
    Adm02EnsureAccessAuditEventType,
    Adm02EnsureAccessAuditOutcomeBucket,
    Adm02EnsureAccessAuditPort,
    Adm02EnsureAccessAuditPrincipalMarker,
    Adm02EnsureAccessAuthorizationPort,
    Adm02EnsureAccessInput,
    Adm02EnsureAccessMutationPort,
    Adm02EnsureAccessOutcome,
    Adm02EnsureAccessRemediationResult,
    Adm02EnsureAccessResult,
    Adm02EnsureAccessSummary,
    Adm02MutationOptInPort,
    IssuanceOperationalState,
)
from app.application.interfaces import SubscriptionSnapshot
from app.shared.correlation import require_correlation_id


class Adm02EnsureAccessHandler:
    """Authorize + mutation-opt-in + identity/subscription/issuance checks + idempotent remediation."""

    def __init__(
        self,
        *,
        authorization: Adm02EnsureAccessAuthorizationPort,
        mutation_opt_in: Adm02MutationOptInPort,
        identity: Adm01IdentityResolvePort,
        subscription: Adm01SubscriptionReadPort,
        issuance: Adm01IssuanceReadPort,
        mutation: Adm02EnsureAccessMutationPort,
        audit: Adm02EnsureAccessAuditPort,
    ) -> None:
        self._authorization = authorization
        self._mutation_opt_in = mutation_opt_in
        self._identity = identity
        self._subscription = subscription
        self._issuance = issuance
        self._mutation = mutation
        self._audit = audit

    async def handle(self, inp: Adm02EnsureAccessInput) -> Adm02EnsureAccessResult:
        cid = inp.correlation_id
        try:
            require_correlation_id(cid)
        except ValueError:
            result = Adm02EnsureAccessResult(
                outcome=Adm02EnsureAccessOutcome.INVALID_INPUT,
                correlation_id=cid,
                summary=None,
            )
            await self._append_safe_audit(result)
            return result

        try:
            allowed = await self._authorization.check_adm02_ensure_access_allowed(
                inp.actor,
                correlation_id=cid,
            )
        except Exception:
            result = Adm02EnsureAccessResult(
                outcome=Adm02EnsureAccessOutcome.DEPENDENCY_FAILURE,
                correlation_id=cid,
                summary=None,
            )
            await self._append_safe_audit(result)
            return result
        if not allowed:
            result = Adm02EnsureAccessResult(
                outcome=Adm02EnsureAccessOutcome.DENIED,
                correlation_id=cid,
                summary=None,
            )
            await self._append_safe_audit(
                result,
                denied_bucket=Adm02EnsureAccessAuditOutcomeBucket.DENIED_UNAUTHORIZED,
            )
            return result

        try:
            mutation_enabled = await self._mutation_opt_in.check_adm02_mutation_opt_in_enabled(correlation_id=cid)
        except Exception:
            result = Adm02EnsureAccessResult(
                outcome=Adm02EnsureAccessOutcome.DEPENDENCY_FAILURE,
                correlation_id=cid,
                summary=None,
            )
            await self._append_safe_audit(result)
            return result
        if not mutation_enabled:
            result = Adm02EnsureAccessResult(
                outcome=Adm02EnsureAccessOutcome.DENIED,
                correlation_id=cid,
                summary=None,
            )
            await self._append_safe_audit(
                result,
                denied_bucket=Adm02EnsureAccessAuditOutcomeBucket.DENIED_MUTATION_OPT_IN_DISABLED,
            )
            return result

        try:
            internal_user_id = await self._identity.resolve_internal_user_id(inp.target, correlation_id=cid)
        except Exception:
            result = Adm02EnsureAccessResult(
                outcome=Adm02EnsureAccessOutcome.DEPENDENCY_FAILURE,
                correlation_id=cid,
                summary=None,
            )
            await self._append_safe_audit(result)
            return result
        if internal_user_id is None:
            result = Adm02EnsureAccessResult(
                outcome=Adm02EnsureAccessOutcome.SUCCESS,
                correlation_id=cid,
                summary=Adm02EnsureAccessSummary(
                    telegram_identity_known=False,
                    subscription_bucket=Adm01SupportSubscriptionBucket.UNKNOWN,
                    access_readiness_bucket=Adm01SupportAccessReadinessBucket.NOT_APPLICABLE_NO_ACTIVE_SUBSCRIPTION,
                    remediation_result=Adm02EnsureAccessRemediationResult.NOOP_IDENTITY_UNKNOWN,
                    recommended_next_action=Adm01SupportNextAction.ASK_USER_TO_USE_STATUS,
                ),
            )
            await self._append_safe_audit(result)
            return result

        try:
            snapshot = await self._subscription.get_subscription_snapshot(internal_user_id)
            pre_issuance = await self._issuance.get_issuance_summary(internal_user_id)
        except Exception:
            result = _safe_failed_result(cid)
            await self._append_safe_audit(result)
            return result

        subscription_bucket = _subscription_bucket_from_snapshot(snapshot)
        if subscription_bucket is not Adm01SupportSubscriptionBucket.ACTIVE:
            result = Adm02EnsureAccessResult(
                outcome=Adm02EnsureAccessOutcome.SUCCESS,
                correlation_id=cid,
                summary=Adm02EnsureAccessSummary(
                    telegram_identity_known=True,
                    subscription_bucket=subscription_bucket,
                    access_readiness_bucket=Adm01SupportAccessReadinessBucket.NOT_APPLICABLE_NO_ACTIVE_SUBSCRIPTION,
                    remediation_result=Adm02EnsureAccessRemediationResult.NOOP_NO_ACTIVE_SUBSCRIPTION,
                    recommended_next_action=Adm01SupportNextAction.INVESTIGATE_BILLING_APPLY,
                ),
            )
            await self._append_safe_audit(result)
            return result

        if pre_issuance.state is IssuanceOperationalState.OK:
            result = Adm02EnsureAccessResult(
                outcome=Adm02EnsureAccessOutcome.SUCCESS,
                correlation_id=cid,
                summary=Adm02EnsureAccessSummary(
                    telegram_identity_known=True,
                    subscription_bucket=Adm01SupportSubscriptionBucket.ACTIVE,
                    access_readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
                    remediation_result=Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY,
                    recommended_next_action=Adm01SupportNextAction.ASK_USER_TO_USE_GET_ACCESS,
                ),
            )
            await self._append_safe_audit(result)
            return result

        try:
            issued_new = await self._mutation.ensure_access_issued(internal_user_id, correlation_id=cid)
            post_issuance = await self._issuance.get_issuance_summary(internal_user_id)
        except Exception:
            result = _safe_failed_result(cid)
            await self._append_safe_audit(result)
            return result

        if post_issuance.state is IssuanceOperationalState.OK:
            result = Adm02EnsureAccessResult(
                outcome=Adm02EnsureAccessOutcome.SUCCESS,
                correlation_id=cid,
                summary=Adm02EnsureAccessSummary(
                    telegram_identity_known=True,
                    subscription_bucket=Adm01SupportSubscriptionBucket.ACTIVE,
                    access_readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
                    remediation_result=(
                        Adm02EnsureAccessRemediationResult.ISSUED_ACCESS
                        if issued_new
                        else Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY
                    ),
                    recommended_next_action=Adm01SupportNextAction.ASK_USER_TO_USE_GET_ACCESS,
                ),
            )
            await self._append_safe_audit(result)
            return result

        result = _safe_failed_result(cid)
        await self._append_safe_audit(result)
        return result

    async def _append_safe_audit(
        self,
        result: Adm02EnsureAccessResult,
        *,
        denied_bucket: Adm02EnsureAccessAuditOutcomeBucket | None = None,
    ) -> None:
        event = Adm02EnsureAccessAuditEvent(
            event_type=Adm02EnsureAccessAuditEventType.ENSURE_ACCESS,
            outcome_bucket=_audit_outcome_bucket(result, denied_bucket=denied_bucket),
            remediation_result=result.summary.remediation_result if result.summary is not None else None,
            readiness_bucket=result.summary.access_readiness_bucket if result.summary is not None else None,
            principal_marker=Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED,
            correlation_id=result.correlation_id,
        )
        try:
            await self._audit.append_ensure_access_event(event)
        except Exception:
            return


def _safe_failed_result(correlation_id: str) -> Adm02EnsureAccessResult:
    return Adm02EnsureAccessResult(
        outcome=Adm02EnsureAccessOutcome.SUCCESS,
        correlation_id=correlation_id,
        summary=Adm02EnsureAccessSummary(
            telegram_identity_known=True,
            subscription_bucket=Adm01SupportSubscriptionBucket.UNKNOWN,
            access_readiness_bucket=Adm01SupportAccessReadinessBucket.UNKNOWN_DUE_TO_INTERNAL_ERROR,
            remediation_result=Adm02EnsureAccessRemediationResult.FAILED_SAFE,
            recommended_next_action=Adm01SupportNextAction.INVESTIGATE_ISSUANCE,
        ),
    )


def _subscription_bucket_from_snapshot(snapshot: SubscriptionSnapshot | None) -> Adm01SupportSubscriptionBucket:
    if snapshot is None:
        return Adm01SupportSubscriptionBucket.UNKNOWN
    state = snapshot.state_label
    if state == "active":
        return Adm01SupportSubscriptionBucket.ACTIVE
    if state == "cancelled":
        return Adm01SupportSubscriptionBucket.CANCELLED
    if state == "expired":
        return Adm01SupportSubscriptionBucket.EXPIRED
    if state in {"inactive", "absent", "not_eligible", "needs_review"}:
        return Adm01SupportSubscriptionBucket.INACTIVE
    return Adm01SupportSubscriptionBucket.UNKNOWN


def _audit_outcome_bucket(
    result: Adm02EnsureAccessResult,
    *,
    denied_bucket: Adm02EnsureAccessAuditOutcomeBucket | None = None,
) -> Adm02EnsureAccessAuditOutcomeBucket:
    if result.outcome is Adm02EnsureAccessOutcome.DENIED:
        return denied_bucket or Adm02EnsureAccessAuditOutcomeBucket.DENIED_UNAUTHORIZED
    if result.outcome is Adm02EnsureAccessOutcome.INVALID_INPUT:
        return Adm02EnsureAccessAuditOutcomeBucket.INVALID_INPUT
    if result.outcome is Adm02EnsureAccessOutcome.DEPENDENCY_FAILURE:
        return Adm02EnsureAccessAuditOutcomeBucket.DEPENDENCY_FAILURE
    summary = result.summary
    if summary is None:
        return Adm02EnsureAccessAuditOutcomeBucket.FAILED_SAFE
    mapping = {
        Adm02EnsureAccessRemediationResult.NOOP_IDENTITY_UNKNOWN: Adm02EnsureAccessAuditOutcomeBucket.NOOP_IDENTITY_UNKNOWN,
        Adm02EnsureAccessRemediationResult.NOOP_NO_ACTIVE_SUBSCRIPTION: Adm02EnsureAccessAuditOutcomeBucket.NOOP_NO_ACTIVE_SUBSCRIPTION,
        Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY: Adm02EnsureAccessAuditOutcomeBucket.NOOP_ACCESS_ALREADY_READY,
        Adm02EnsureAccessRemediationResult.ISSUED_ACCESS: Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS,
        Adm02EnsureAccessRemediationResult.FAILED_SAFE: Adm02EnsureAccessAuditOutcomeBucket.FAILED_SAFE,
    }
    return mapping.get(summary.remediation_result, Adm02EnsureAccessAuditOutcomeBucket.FAILED_SAFE)


class NoopAdm02EnsureAccessAuditSink(Adm02EnsureAccessAuditPort):
    async def append_ensure_access_event(self, event: Adm02EnsureAccessAuditEvent) -> None:
        _ = event
