"""ADM-01 lookup orchestration (read-only; no transport, audit, or persistence here)."""

from __future__ import annotations

from app.admin_support.contracts import (
    Adm01AuthorizationPort,
    Adm01EntitlementReadPort,
    Adm01IdentityResolvePort,
    Adm01IssuanceReadPort,
    Adm01LookupInput,
    Adm01LookupOutcome,
    Adm01LookupResult,
    Adm01LookupSummary,
    Adm01PolicyReadPort,
    Adm01RedactionPort,
    Adm01SubscriptionReadPort,
    Adm01SubscriptionStatusSummary,
    Adm01SupportAccessReadinessBucket,
    Adm01SupportNextAction,
    Adm01SupportReadinessSummary,
    Adm01SupportSubscriptionBucket,
    AdminPolicyFlag,
    EntitlementSummary,
    EntitlementSummaryCategory,
    IssuanceOperationalState,
    IssuanceOperationalSummary,
    RedactionMarker,
)
from app.application.interfaces import SubscriptionSnapshot
from app.shared.correlation import require_correlation_id


class Adm01LookupHandler:
    """Validate correlation → authorize ADM-01 → resolve identity → read summaries → optional redaction."""

    def __init__(
        self,
        authorization: Adm01AuthorizationPort,
        identity: Adm01IdentityResolvePort,
        subscription: Adm01SubscriptionReadPort,
        entitlement: Adm01EntitlementReadPort,
        issuance: Adm01IssuanceReadPort,
        policy: Adm01PolicyReadPort,
        redaction: Adm01RedactionPort | None = None,
    ) -> None:
        self._authorization = authorization
        self._identity = identity
        self._subscription = subscription
        self._entitlement = entitlement
        self._issuance = issuance
        self._policy = policy
        self._redaction = redaction

    async def handle(self, inp: Adm01LookupInput) -> Adm01LookupResult:
        cid = inp.correlation_id
        try:
            require_correlation_id(cid)
        except ValueError:
            return Adm01LookupResult(
                outcome=Adm01LookupOutcome.INVALID_INPUT,
                correlation_id=cid,
                summary=None,
            )

        try:
            allowed = await self._authorization.check_adm01_lookup_allowed(
                inp.actor,
                correlation_id=cid,
            )
        except Exception:
            return Adm01LookupResult(
                outcome=Adm01LookupOutcome.DEPENDENCY_FAILURE,
                correlation_id=cid,
                summary=None,
            )
        if not allowed:
            return Adm01LookupResult(
                outcome=Adm01LookupOutcome.DENIED,
                correlation_id=cid,
                summary=None,
            )

        try:
            internal_user_id = await self._identity.resolve_internal_user_id(
                inp.target,
                correlation_id=cid,
            )
        except Exception:
            return Adm01LookupResult(
                outcome=Adm01LookupOutcome.DEPENDENCY_FAILURE,
                correlation_id=cid,
                summary=None,
            )
        if internal_user_id is None:
            return Adm01LookupResult(
                outcome=Adm01LookupOutcome.SUCCESS,
                correlation_id=cid,
                summary=Adm01LookupSummary(
                    subscription=Adm01SubscriptionStatusSummary(snapshot=None),
                    entitlement=EntitlementSummary(category=EntitlementSummaryCategory.UNKNOWN),
                    policy_flag=AdminPolicyFlag.UNKNOWN,
                    issuance=IssuanceOperationalSummary(state=IssuanceOperationalState.UNKNOWN),
                    support_readiness=Adm01SupportReadinessSummary(
                        telegram_identity_known=False,
                        subscription_bucket=Adm01SupportSubscriptionBucket.UNKNOWN,
                        access_readiness_bucket=Adm01SupportAccessReadinessBucket.NOT_APPLICABLE_NO_ACTIVE_SUBSCRIPTION,
                        recommended_next_action=Adm01SupportNextAction.ASK_USER_TO_USE_STATUS,
                    ),
                    redaction=RedactionMarker.NONE,
                ),
            )

        try:
            snapshot = await self._subscription.get_subscription_snapshot(internal_user_id)
            entitlement = await self._entitlement.get_entitlement_summary(internal_user_id)
            issuance = await self._issuance.get_issuance_summary(internal_user_id)
            policy_flag = await self._policy.get_policy_flag(internal_user_id)
        except Exception:
            return Adm01LookupResult(
                outcome=Adm01LookupOutcome.SUCCESS,
                correlation_id=cid,
                summary=Adm01LookupSummary(
                    subscription=Adm01SubscriptionStatusSummary(snapshot=None),
                    entitlement=EntitlementSummary(category=EntitlementSummaryCategory.UNKNOWN),
                    policy_flag=AdminPolicyFlag.UNKNOWN,
                    issuance=IssuanceOperationalSummary(state=IssuanceOperationalState.UNKNOWN),
                    support_readiness=Adm01SupportReadinessSummary(
                        telegram_identity_known=True,
                        subscription_bucket=Adm01SupportSubscriptionBucket.UNKNOWN,
                        access_readiness_bucket=Adm01SupportAccessReadinessBucket.UNKNOWN_DUE_TO_INTERNAL_ERROR,
                        recommended_next_action=Adm01SupportNextAction.INVESTIGATE_ISSUANCE,
                    ),
                    redaction=RedactionMarker.NONE,
                ),
            )

        subscription_bucket = _subscription_bucket_from_snapshot(snapshot)
        readiness_bucket = _access_readiness_bucket(subscription_bucket, issuance.state)
        next_action = _recommended_next_action(readiness_bucket)
        summary = Adm01LookupSummary(
            subscription=Adm01SubscriptionStatusSummary(snapshot=snapshot),
            entitlement=entitlement,
            policy_flag=policy_flag,
            issuance=issuance,
            support_readiness=Adm01SupportReadinessSummary(
                telegram_identity_known=True,
                subscription_bucket=subscription_bucket,
                access_readiness_bucket=readiness_bucket,
                recommended_next_action=next_action,
            ),
            redaction=RedactionMarker.NONE,
        )
        if self._redaction is not None:
            try:
                summary = await self._redaction.redact_lookup_summary(summary)
            except Exception:
                return Adm01LookupResult(
                    outcome=Adm01LookupOutcome.DEPENDENCY_FAILURE,
                    correlation_id=cid,
                    summary=None,
                )

        return Adm01LookupResult(
            outcome=Adm01LookupOutcome.SUCCESS,
            correlation_id=cid,
            summary=summary,
        )


def _subscription_bucket_from_snapshot(
    snapshot: SubscriptionSnapshot | None,
) -> Adm01SupportSubscriptionBucket:
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


def _access_readiness_bucket(
    subscription_bucket: Adm01SupportSubscriptionBucket,
    issuance_state: IssuanceOperationalState,
) -> Adm01SupportAccessReadinessBucket:
    if subscription_bucket is not Adm01SupportSubscriptionBucket.ACTIVE:
        return Adm01SupportAccessReadinessBucket.NOT_APPLICABLE_NO_ACTIVE_SUBSCRIPTION
    if issuance_state is IssuanceOperationalState.OK:
        return Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY
    if issuance_state in {
        IssuanceOperationalState.NONE,
        IssuanceOperationalState.DEGRADED,
        IssuanceOperationalState.FAILED,
        IssuanceOperationalState.UNKNOWN,
    }:
        return Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_NOT_READY
    return Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_NOT_READY


def _recommended_next_action(
    readiness_bucket: Adm01SupportAccessReadinessBucket,
) -> Adm01SupportNextAction:
    if readiness_bucket is Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY:
        return Adm01SupportNextAction.ASK_USER_TO_USE_GET_ACCESS
    if readiness_bucket is Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_NOT_READY:
        return Adm01SupportNextAction.INVESTIGATE_ISSUANCE
    if readiness_bucket is Adm01SupportAccessReadinessBucket.UNKNOWN_DUE_TO_INTERNAL_ERROR:
        return Adm01SupportNextAction.INVESTIGATE_ISSUANCE
    return Adm01SupportNextAction.INVESTIGATE_BILLING_APPLY
