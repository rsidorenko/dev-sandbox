"""Pure tests: transport presentation mapping from handler results."""

from __future__ import annotations

from dataclasses import fields

from app.application.handlers import BootstrapIdentityResult, GetSubscriptionStatusResult
from app.application.telegram_access_resend import TelegramAccessResendOutcome, TelegramAccessResendResult
from app.bot_transport.presentation import (
    TransportAccessResendCode,
    TransportBootstrapCode,
    TransportHelpCode,
    TransportNextActionHint,
    TransportResponseCategory,
    TransportSafeResponse,
    TransportStatusCode,
    map_access_resend_to_transport,
    map_bootstrap_identity_to_transport,
    map_get_subscription_status_to_transport,
    map_slice1_help_to_transport,
)
from app.security.errors import UserSafeErrorCode
from app.shared.correlation import new_correlation_id
from app.shared.types import OperationOutcomeCategory, SafeUserStatusCategory


def test_bootstrap_success_maps_to_identity_ready() -> None:
    cid = new_correlation_id()
    r = map_bootstrap_identity_to_transport(
        BootstrapIdentityResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            internal_user_id="u1",
            user_safe=None,
            idempotent_replay=False,
        ),
    )
    assert r == TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportBootstrapCode.IDENTITY_READY.value,
        correlation_id=cid,
        next_action_hint=None,
        replay_suppresses_outbound=False,
        uc01_idempotency_key=None,
    )
    assert r.replay_suppresses_outbound is False


def test_bootstrap_idempotent_replay_same_as_success() -> None:
    cid = new_correlation_id()
    r = map_bootstrap_identity_to_transport(
        BootstrapIdentityResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            internal_user_id="u1",
            user_safe=None,
            idempotent_replay=True,
        ),
    )
    assert r.category is TransportResponseCategory.SUCCESS
    assert r.code == TransportBootstrapCode.IDENTITY_READY.value
    assert r.correlation_id == cid
    assert r.replay_suppresses_outbound is True


def test_bootstrap_validation_failure_maps_to_error_only() -> None:
    cid = new_correlation_id()
    r = map_bootstrap_identity_to_transport(
        BootstrapIdentityResult(
            outcome=OperationOutcomeCategory.VALIDATION_FAILURE,
            correlation_id=cid,
            internal_user_id=None,
            user_safe=UserSafeErrorCode.INVALID_INPUT,
            idempotent_replay=False,
        ),
    )
    assert r.category is TransportResponseCategory.ERROR
    assert r.code == "invalid_input"


def test_bootstrap_dependency_failure_maps_to_try_again() -> None:
    cid = new_correlation_id()
    r = map_bootstrap_identity_to_transport(
        BootstrapIdentityResult(
            outcome=OperationOutcomeCategory.RETRYABLE_DEPENDENCY,
            correlation_id=cid,
            internal_user_id=None,
            user_safe=UserSafeErrorCode.TRY_AGAIN_LATER,
            idempotent_replay=False,
        ),
    )
    assert r.category is TransportResponseCategory.ERROR
    assert r.code == "try_again_later"


def test_status_unknown_user_guidance() -> None:
    cid = new_correlation_id()
    r = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.NOT_FOUND,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.NEEDS_BOOTSTRAP,
            user_safe=UserSafeErrorCode.NOT_REGISTERED,
        ),
    )
    assert r.category is TransportResponseCategory.GUIDANCE
    assert r.code == TransportStatusCode.NEEDS_ONBOARDING.value
    assert r.next_action_hint == TransportNextActionHint.COMPLETE_BOOTSTRAP.value


def test_status_inactive_snapshot_fail_closed() -> None:
    cid = new_correlation_id()
    r = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE,
            user_safe=None,
        ),
    )
    assert r.category is TransportResponseCategory.SUCCESS
    assert r.code == "inactive_or_not_eligible"
    assert r.replay_suppresses_outbound is False


def test_status_subscription_active_maps_stably() -> None:
    cid = new_correlation_id()
    r = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.SUBSCRIPTION_ACTIVE,
            user_safe=None,
        ),
    )
    assert r.category is TransportResponseCategory.SUCCESS
    assert r.code == TransportStatusCode.SUBSCRIPTION_ACTIVE.value


def test_status_subscription_active_access_not_ready_maps_stably() -> None:
    cid = new_correlation_id()
    r = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY,
            user_safe=None,
        ),
    )
    assert r.category is TransportResponseCategory.SUCCESS
    assert r.code == TransportStatusCode.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY.value


def test_status_subscription_active_access_ready_maps_stably() -> None:
    cid = new_correlation_id()
    r = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_READY,
            user_safe=None,
        ),
    )
    assert r.category is TransportResponseCategory.SUCCESS
    assert r.code == TransportStatusCode.SUBSCRIPTION_ACTIVE_ACCESS_READY.value


def test_transport_response_has_no_outcome_or_internal_fields() -> None:
    cid = new_correlation_id()
    map_bootstrap_identity_to_transport(
        BootstrapIdentityResult(
            outcome=OperationOutcomeCategory.INTERNAL_FAILURE,
            correlation_id=cid,
            internal_user_id=None,
            user_safe=UserSafeErrorCode.SERVICE_UNAVAILABLE,
            idempotent_replay=False,
        ),
    )
    field_names = {f.name for f in fields(TransportSafeResponse)}
    assert field_names == {
        "category",
        "code",
        "correlation_id",
        "next_action_hint",
        "replay_suppresses_outbound",
        "uc01_idempotency_key",
        "active_until_ymd",
        "subscription_active_recovery_followup",
    }


def test_correlation_id_preserved() -> None:
    cid = new_correlation_id()
    r = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.NEEDS_REVIEW,
            user_safe=None,
        ),
    )
    assert r.correlation_id == cid


def test_response_codes_exclude_billing_issuance_admin() -> None:
    cid = new_correlation_id()
    r = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE,
            user_safe=None,
        ),
    )
    lowered = r.code.lower()
    assert "billing" not in lowered
    assert "issuance" not in lowered
    assert "admin" not in lowered


def test_map_slice1_help_read_only() -> None:
    cid = new_correlation_id()
    r = map_slice1_help_to_transport(cid)
    assert r == TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportHelpCode.SLICE1_HELP.value,
        correlation_id=cid,
        next_action_hint=None,
        replay_suppresses_outbound=False,
        uc01_idempotency_key=None,
    )


def test_access_resend_maps_to_stable_transport_codes() -> None:
    cid = new_correlation_id()
    not_enabled = map_access_resend_to_transport(
        TelegramAccessResendResult(
            outcome=TelegramAccessResendOutcome.NOT_ENABLED,
            correlation_id=cid,
        )
    )
    assert not_enabled.code == TransportAccessResendCode.NOT_ENABLED.value
    accepted = map_access_resend_to_transport(
        TelegramAccessResendResult(
            outcome=TelegramAccessResendOutcome.RESEND_ACCEPTED,
            correlation_id=cid,
        )
    )
    assert accepted.code == TransportAccessResendCode.RESEND_ACCEPTED.value
    cooldown = map_access_resend_to_transport(
        TelegramAccessResendResult(
            outcome=TelegramAccessResendOutcome.COOLDOWN,
            correlation_id=cid,
        )
    )
    assert cooldown.code == TransportAccessResendCode.COOLDOWN.value
