"""Pure tests: TransportSafeResponse → Telegram outbound plan (no SDK, no copy)."""

from __future__ import annotations

import dataclasses
import enum

from app.application.handlers import BootstrapIdentityResult, GetSubscriptionStatusResult
from app.bot_transport.outbound import (
    OutboundKeyboardMarker,
    OutboundMessageKey,
    OutboundNextActionKey,
    OutboundPlanCategory,
    TelegramOutboundPlan,
    map_transport_safe_to_outbound_plan,
)
from app.bot_transport.presentation import (
    TransportAccessResendCode,
    TransportErrorCode,
    TransportResponseCategory,
    TransportSafeResponse,
    TransportStatusCode,
    TransportStorefrontCode,
    TransportSupportCode,
    map_bootstrap_identity_to_transport,
    map_get_subscription_status_to_transport,
    map_slice1_help_to_transport,
)
from app.security.errors import UserSafeErrorCode
from app.shared.correlation import new_correlation_id
from app.shared.types import OperationOutcomeCategory, SafeUserStatusCategory


def _assert_no_foreign_enums_on_plan(plan: TelegramOutboundPlan) -> None:
    """Outbound plan must not carry application/domain enum instances."""
    for field in dataclasses.fields(plan):
        val = getattr(plan, field.name)
        if isinstance(val, enum.Enum):
            assert type(val).__module__ == "app.bot_transport.outbound", (
                f"unexpected enum on plan field {field.name}: {type(val)}"
            )


def _plan_strings_lower(plan: TelegramOutboundPlan) -> str:
    parts = [
        plan.message_key,
        plan.next_action_key or "",
        plan.keyboard_marker,
        plan.category.value,
    ]
    return " ".join(parts).lower()


def test_help_read_only_outbound_message_key() -> None:
    cid = new_correlation_id()
    safe = map_slice1_help_to_transport(cid)
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.message_key == OutboundMessageKey.SLICE1_HELP.value
    assert plan.keyboard_marker == OutboundKeyboardMarker.STOREFRONT_MAIN.value
    assert plan.replay_suppresses_outbound is False
    assert plan.uc01_idempotency_key is None
    assert plan.correlation_id == cid


def test_bootstrap_success_stable_message_key() -> None:
    cid = new_correlation_id()
    safe = map_bootstrap_identity_to_transport(
        BootstrapIdentityResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            internal_user_id="u1",
            user_safe=None,
            idempotent_replay=False,
        ),
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.message_key == OutboundMessageKey.IDENTITY_READY.value
    assert plan.category is OutboundPlanCategory.SUCCESS
    assert plan.correlation_id == cid


def test_bootstrap_idempotent_replay_sets_outbound_suppress_flag() -> None:
    cid = new_correlation_id()
    first = map_bootstrap_identity_to_transport(
        BootstrapIdentityResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            internal_user_id="u1",
            user_safe=None,
            idempotent_replay=False,
        ),
    )
    replay = map_bootstrap_identity_to_transport(
        BootstrapIdentityResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            internal_user_id="u1",
            user_safe=None,
            idempotent_replay=True,
        ),
    )
    p_first = map_transport_safe_to_outbound_plan(first)
    p_replay = map_transport_safe_to_outbound_plan(replay)
    assert p_first.message_key == p_replay.message_key == OutboundMessageKey.IDENTITY_READY.value
    assert p_first.replay_suppresses_outbound is False
    assert p_replay.replay_suppresses_outbound is True


def test_onboarding_guidance_has_onboarding_action_hint() -> None:
    cid = new_correlation_id()
    safe = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.NOT_FOUND,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.NEEDS_BOOTSTRAP,
            user_safe=UserSafeErrorCode.NOT_REGISTERED,
        ),
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.category is OutboundPlanCategory.GUIDANCE
    assert plan.message_key == OutboundMessageKey.NEEDS_ONBOARDING.value
    assert plan.next_action_key == OutboundNextActionKey.COMPLETE_BOOTSTRAP.value
    assert plan.keyboard_marker == OutboundKeyboardMarker.PRIMARY_ONBOARDING.value
    assert plan.correlation_id == cid


def test_inactive_not_eligible_fail_closed_message_key() -> None:
    cid = new_correlation_id()
    safe = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE,
            user_safe=None,
        ),
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.category is OutboundPlanCategory.SUCCESS
    assert plan.message_key == OutboundMessageKey.INACTIVE_OR_NOT_ELIGIBLE.value
    assert plan.next_action_key is None


def test_needs_review_status_message_key() -> None:
    cid = new_correlation_id()
    safe = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.NEEDS_REVIEW,
            user_safe=None,
        ),
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.message_key == OutboundMessageKey.NEEDS_REVIEW.value
    assert plan.category is OutboundPlanCategory.SUCCESS


def test_subscription_active_status_message_key() -> None:
    cid = new_correlation_id()
    safe = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.SUBSCRIPTION_ACTIVE,
            user_safe=None,
        ),
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.message_key == OutboundMessageKey.SUBSCRIPTION_ACTIVE.value
    assert plan.category is OutboundPlanCategory.SUCCESS


def test_subscription_active_access_not_ready_status_message_key() -> None:
    cid = new_correlation_id()
    safe = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY,
            user_safe=None,
        ),
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.message_key == OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY.value
    assert plan.category is OutboundPlanCategory.SUCCESS


def test_subscription_active_access_ready_status_message_key() -> None:
    cid = new_correlation_id()
    safe = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_READY,
            user_safe=None,
        ),
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.message_key == OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_READY.value
    assert plan.category is OutboundPlanCategory.SUCCESS


def test_error_invalid_input_safe_key() -> None:
    cid = new_correlation_id()
    safe = TransportSafeResponse(
        category=TransportResponseCategory.ERROR,
        code="invalid_input",
        correlation_id=cid,
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.category is OutboundPlanCategory.ERROR
    assert plan.message_key == OutboundMessageKey.INVALID_INPUT.value


def test_error_try_again_later_safe_key() -> None:
    cid = new_correlation_id()
    safe = TransportSafeResponse(
        category=TransportResponseCategory.ERROR,
        code="try_again_later",
        correlation_id=cid,
    )
    assert map_transport_safe_to_outbound_plan(safe).message_key == OutboundMessageKey.TRY_AGAIN_LATER.value


def test_error_service_unavailable_safe_key() -> None:
    cid = new_correlation_id()
    safe = TransportSafeResponse(
        category=TransportResponseCategory.ERROR,
        code="service_unavailable",
        correlation_id=cid,
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.message_key == OutboundMessageKey.SERVICE_UNAVAILABLE.value
    assert plan.category is OutboundPlanCategory.ERROR


def test_error_telegram_command_rate_limited_safe_key() -> None:
    cid = new_correlation_id()
    safe = TransportSafeResponse(
        category=TransportResponseCategory.ERROR,
        code=TransportErrorCode.TELEGRAM_COMMAND_RATE_LIMITED.value,
        correlation_id=cid,
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.message_key == OutboundMessageKey.TELEGRAM_COMMAND_RATE_LIMITED.value
    assert plan.category is OutboundPlanCategory.ERROR


def test_outbound_plan_has_no_leaked_internal_enums() -> None:
    cid = new_correlation_id()
    safe = map_bootstrap_identity_to_transport(
        BootstrapIdentityResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            internal_user_id="u1",
            user_safe=None,
            idempotent_replay=False,
        ),
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    _assert_no_foreign_enums_on_plan(plan)


def test_outbound_mapping_excludes_billing_issuance_admin_concepts() -> None:
    cid = new_correlation_id()
    safes = (
        map_get_subscription_status_to_transport(
            GetSubscriptionStatusResult(
                outcome=OperationOutcomeCategory.SUCCESS,
                correlation_id=cid,
                safe_status=SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE,
                user_safe=None,
            ),
        ),
        TransportSafeResponse(
            category=TransportResponseCategory.ERROR,
            code="invalid_input",
            correlation_id=cid,
        ),
    )
    for safe in safes:
        plan = map_transport_safe_to_outbound_plan(safe)
        blob = _plan_strings_lower(plan)
        assert "billing" not in blob
        assert "issuance" not in blob
        assert "admin" not in blob


def test_correlation_id_pass_through() -> None:
    cid = new_correlation_id()
    safe = TransportSafeResponse(
        category=TransportResponseCategory.ERROR,
        code="try_again_later",
        correlation_id=cid,
    )
    assert map_transport_safe_to_outbound_plan(safe).correlation_id == cid


def test_no_product_text_embedded_in_plan_fields() -> None:
    cid = new_correlation_id()
    safe = map_get_subscription_status_to_transport(
        GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.NOT_FOUND,
            correlation_id=cid,
            safe_status=SafeUserStatusCategory.NEEDS_BOOTSTRAP,
            user_safe=UserSafeErrorCode.NOT_REGISTERED,
        ),
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    for field in dataclasses.fields(plan):
        val = getattr(plan, field.name)
        if isinstance(val, str):
            assert "\n" not in val
            assert "\t" not in val
            assert val == val.strip()
            if field.name != "correlation_id":
                assert " " not in val
                assert not any(c.isupper() for c in val if c.isalpha())


def test_guidance_without_next_action_hint_still_safe() -> None:
    cid = new_correlation_id()
    safe = TransportSafeResponse(
        category=TransportResponseCategory.GUIDANCE,
        code=TransportStatusCode.NEEDS_ONBOARDING.value,
        correlation_id=cid,
        next_action_hint=None,
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.message_key == OutboundMessageKey.NEEDS_ONBOARDING.value
    assert plan.next_action_key is None
    assert plan.keyboard_marker == OutboundKeyboardMarker.NONE.value


def test_resend_transport_codes_map_to_safe_outbound_keys() -> None:
    cid = new_correlation_id()
    safe_not_enabled = TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportAccessResendCode.NOT_ENABLED.value,
        correlation_id=cid,
    )
    plan0 = map_transport_safe_to_outbound_plan(safe_not_enabled)
    assert plan0.message_key == OutboundMessageKey.RESEND_ACCESS_NOT_ENABLED.value
    safe = TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportAccessResendCode.RESEND_ACCEPTED.value,
        correlation_id=cid,
    )
    plan = map_transport_safe_to_outbound_plan(safe)
    assert plan.message_key == OutboundMessageKey.RESEND_ACCESS_ACCEPTED.value
    safe_cooldown = TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportAccessResendCode.COOLDOWN.value,
        correlation_id=cid,
    )
    plan2 = map_transport_safe_to_outbound_plan(safe_cooldown)
    assert plan2.message_key == OutboundMessageKey.RESEND_ACCESS_COOLDOWN.value


def test_storefront_transport_codes_map_to_outbound_keys() -> None:
    cid = new_correlation_id()
    safe_plans = TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportStorefrontCode.STORE_PLANS.value,
        correlation_id=cid,
    )
    safe_buy = TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportStorefrontCode.STORE_BUY.value,
        correlation_id=cid,
    )
    safe_success = TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportStorefrontCode.STORE_SUCCESS.value,
        correlation_id=cid,
    )
    safe_success_active = TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportStorefrontCode.STORE_SUCCESS_ACTIVE.value,
        correlation_id=cid,
    )
    safe_renew = TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportStorefrontCode.STORE_RENEW.value,
        correlation_id=cid,
    )
    safe_support_menu = TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportSupportCode.SUPPORT_MENU.value,
        correlation_id=cid,
    )
    safe_support_contact = TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=TransportSupportCode.SUPPORT_CONTACT.value,
        correlation_id=cid,
    )
    assert map_transport_safe_to_outbound_plan(safe_plans).message_key == OutboundMessageKey.STORE_PLANS.value
    assert map_transport_safe_to_outbound_plan(safe_buy).message_key == OutboundMessageKey.STORE_BUY.value
    assert map_transport_safe_to_outbound_plan(safe_success).message_key == OutboundMessageKey.STORE_SUCCESS.value
    assert (
        map_transport_safe_to_outbound_plan(safe_success_active).message_key
        == OutboundMessageKey.STORE_SUCCESS_ACTIVE.value
    )
    assert map_transport_safe_to_outbound_plan(safe_renew).message_key == OutboundMessageKey.STORE_RENEW.value
    assert map_transport_safe_to_outbound_plan(safe_support_menu).message_key == OutboundMessageKey.SUPPORT_MENU.value
    assert (
        map_transport_safe_to_outbound_plan(safe_support_contact).message_key
        == OutboundMessageKey.SUPPORT_CONTACT.value
    )
