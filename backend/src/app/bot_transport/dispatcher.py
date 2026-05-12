"""Thin slice-1 transport dispatcher: normalize → handlers → presentation (no Telegram SDK)."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

from app.application.bootstrap import Slice1Composition
from app.application.handlers import GetSubscriptionStatusInput
from app.application.telegram_command_rate_limit import TelegramCommandRateLimitKey
from app.application.telegram_command_rate_limit_telemetry import (
    TelegramCommandRateLimitDecisionEvent,
    TelegramRateLimitDecision,
    command_bucket_from_key,
    window_bucket_from_key,
)
from app.bot_transport.normalized import (
    NormalizedCallback,
    NormalizedSlice1Bootstrap,
    NormalizedSlice1Help,
    NormalizedSlice1Menu,
    NormalizedSlice1Buy,
    NormalizedSlice1Plans,
    NormalizedSlice1Rejected,
    NormalizedSlice1ResendAccess,
    NormalizedSlice1Renew,
    NormalizedSlice1Success,
    NormalizedSlice1SupportContact,
    NormalizedSlice1SupportMenu,
    NormalizedSlice1Status,
    TransportIncomingEnvelope,
    parse_slice1_transport,
)
from app.bot_transport.presentation import (
    TransportErrorCode,
    TransportResponseCategory,
    TransportSafeResponse,
    map_bootstrap_identity_to_transport,
    map_access_resend_to_transport,
    map_get_subscription_status_to_transport,
    map_slice1_help_to_transport,
    map_slice1_storefront_to_transport,
    map_slice1_support_to_transport,
    TransportStorefrontCode,
    TransportSupportCode,
)
from app.shared.types import OperationOutcomeCategory, SafeUserStatusCategory

_LOGGER = logging.getLogger(__name__)


def _normalization_reject_response(envelope: TransportIncomingEnvelope) -> TransportSafeResponse:
    """Map normalization rejection to transport-safe error (no handler invocation)."""
    return TransportSafeResponse(
        category=TransportResponseCategory.ERROR,
        code=TransportErrorCode.INVALID_INPUT.value,
        correlation_id=envelope.correlation_id,
        next_action_hint=None,
        uc01_idempotency_key=None,
    )


def _rate_limited_response(correlation_id: str) -> TransportSafeResponse:
    return TransportSafeResponse(
        category=TransportResponseCategory.ERROR,
        code=TransportErrorCode.TELEGRAM_COMMAND_RATE_LIMITED.value,
        correlation_id=correlation_id,
        next_action_hint=None,
        replay_suppresses_outbound=False,
        uc01_idempotency_key=None,
    )


def _update_marker(envelope: TransportIncomingEnvelope) -> Literal["present", "absent"]:
    return "present" if envelope.telegram_update_id is not None else "absent"


async def _emit_rate_limit_decision(
    composition: Slice1Composition,
    *,
    envelope: TransportIncomingEnvelope,
    command_key: TelegramCommandRateLimitKey,
    decision: TelegramRateLimitDecision,
    correlation_id: str,
) -> None:
    event = TelegramCommandRateLimitDecisionEvent(
        event_type="telegram_command_rate_limit_decision",
        command_bucket=command_bucket_from_key(command_key),
        decision=decision,
        limit_window_bucket=window_bucket_from_key(command_key),
        principal_marker="telegram_user_redacted",
        correlation_id=correlation_id,
        update_marker=_update_marker(envelope),
    )
    try:
        await composition.command_rate_limit_telemetry.emit_decision(event)
    except Exception:
        _LOGGER.debug(
            "bot_transport.telegram.command_rate_limit.telemetry_dropped",
            exc_info=True,
        )


def _dispatch_callback(
    cb: NormalizedCallback,
    envelope: TransportIncomingEnvelope,
) -> TransportSafeResponse:
    """Route inline callback to storefront UI actions. Returns a transport response with storefront code."""
    from app.bot_transport.storefront_ui import (
        CB_BUY_VPN,
        CB_MAIN_MENU,
        CB_HELP,
        CB_REFERRAL,
        CB_BALANCE,
        CB_SETTINGS,
        CB_MY_SUB,
        CB_MY_KEYS,
        CB_SUB_URL,
        CB_ROUTER,
        CB_PLAN,
        CB_DEVICES,
        CB_CONFIRM_PAY,
    )
    action = cb.action
    # Map callback actions to existing transport storefront codes
    if action in (CB_MAIN_MENU,):
        return map_slice1_storefront_to_transport(TransportStorefrontCode.STORE_MENU, cb.correlation_id)
    if action in (CB_BUY_VPN,):
        return map_slice1_storefront_to_transport(TransportStorefrontCode.STORE_PLANS, cb.correlation_id)
    if action in (CB_MY_SUB,):
        return map_slice1_storefront_to_transport(TransportStorefrontCode.STORE_SUCCESS, cb.correlation_id)
    if action in (CB_HELP,):
        return map_slice1_help_to_transport(cb.correlation_id)
    # For new storefront actions, return a storefront response with the action as code
    return TransportSafeResponse(
        category=TransportResponseCategory.SUCCESS,
        code=action,
        correlation_id=cb.correlation_id,
    )


async def dispatch_slice1_transport(
    envelope: TransportIncomingEnvelope,
    composition: Slice1Composition,
) -> TransportSafeResponse:
    """
    Parse ingress, route to UC-01 / UC-02 handlers (or /help) on the given composition, map to transport.
    Unknown commands and invalid transport fields are rejected before handlers; correlation id is echoed.
    """
    parsed = parse_slice1_transport(envelope)

    # Inline callback buttons → storefront UI (bypasses transport-safe pipeline)
    if isinstance(parsed, NormalizedCallback):
        return _dispatch_callback(parsed, envelope)

    match parsed:
        case NormalizedSlice1Rejected():
            return _normalization_reject_response(envelope)
        case NormalizedSlice1Help(correlation_id=help_cid):
            return map_slice1_help_to_transport(help_cid)
        case NormalizedSlice1Menu(correlation_id=menu_cid):
            return map_slice1_storefront_to_transport(TransportStorefrontCode.STORE_MENU, menu_cid)
        case NormalizedSlice1Plans(correlation_id=cid):
            return map_slice1_storefront_to_transport(TransportStorefrontCode.STORE_PLANS, cid)
        case NormalizedSlice1Buy(correlation_id=cid):
            return map_slice1_storefront_to_transport(TransportStorefrontCode.STORE_BUY, cid)
        case NormalizedSlice1Success(correlation_id=cid):
            status_result = await composition.get_status.handle(
                GetSubscriptionStatusInput(
                    telegram_user_id=envelope.telegram_user_id,
                    correlation_id=cid,
                )
            )
            if status_result.safe_status in (
                SafeUserStatusCategory.SUBSCRIPTION_ACTIVE,
                SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY,
                SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_READY,
            ):
                return map_slice1_storefront_to_transport(TransportStorefrontCode.STORE_SUCCESS_ACTIVE, cid)
            return map_slice1_storefront_to_transport(TransportStorefrontCode.STORE_SUCCESS, cid)
        case NormalizedSlice1Renew(correlation_id=cid):
            return map_slice1_storefront_to_transport(TransportStorefrontCode.STORE_RENEW, cid)
        case NormalizedSlice1SupportMenu(correlation_id=cid):
            allowed = await composition.command_rate_limiter.allow(
                telegram_user_id=envelope.telegram_user_id,
                command_key=TelegramCommandRateLimitKey.SUPPORT,
            )
            if not allowed:
                await _emit_rate_limit_decision(
                    composition,
                    envelope=envelope,
                    command_key=TelegramCommandRateLimitKey.SUPPORT,
                    decision="limited",
                    correlation_id=cid,
                )
                return _rate_limited_response(cid)
            await _emit_rate_limit_decision(
                composition,
                envelope=envelope,
                command_key=TelegramCommandRateLimitKey.SUPPORT,
                decision="allowed",
                correlation_id=cid,
            )
            return map_slice1_support_to_transport(TransportSupportCode.SUPPORT_MENU, cid)
        case NormalizedSlice1SupportContact(correlation_id=cid):
            allowed = await composition.command_rate_limiter.allow(
                telegram_user_id=envelope.telegram_user_id,
                command_key=TelegramCommandRateLimitKey.SUPPORT,
            )
            if not allowed:
                await _emit_rate_limit_decision(
                    composition,
                    envelope=envelope,
                    command_key=TelegramCommandRateLimitKey.SUPPORT,
                    decision="limited",
                    correlation_id=cid,
                )
                return _rate_limited_response(cid)
            await _emit_rate_limit_decision(
                composition,
                envelope=envelope,
                command_key=TelegramCommandRateLimitKey.SUPPORT,
                decision="allowed",
                correlation_id=cid,
            )
            return map_slice1_support_to_transport(TransportSupportCode.SUPPORT_CONTACT, cid)
        case NormalizedSlice1Bootstrap(input=bootstrap_input):
            result = await composition.bootstrap.handle(bootstrap_input)
            return map_bootstrap_identity_to_transport(result)
        case NormalizedSlice1Status(input=status_input):
            allowed = await composition.command_rate_limiter.allow(
                telegram_user_id=envelope.telegram_user_id,
                command_key=TelegramCommandRateLimitKey.STATUS,
            )
            if not allowed:
                await _emit_rate_limit_decision(
                    composition,
                    envelope=envelope,
                    command_key=TelegramCommandRateLimitKey.STATUS,
                    decision="limited",
                    correlation_id=status_input.correlation_id,
                )
                return _rate_limited_response(status_input.correlation_id)
            await _emit_rate_limit_decision(
                composition,
                envelope=envelope,
                command_key=TelegramCommandRateLimitKey.STATUS,
                decision="allowed",
                correlation_id=status_input.correlation_id,
            )
            result = await composition.get_status.handle(status_input)
            transport = map_get_subscription_status_to_transport(result)
            if (
                result.outcome is OperationOutcomeCategory.SUCCESS
                and result.active_until_utc is not None
                and result.active_until_utc > datetime.now(UTC)
                and result.safe_status
                in (
                    SafeUserStatusCategory.SUBSCRIPTION_ACTIVE,
                    SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY,
                    SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_READY,
                )
            ):
                return replace(transport, subscription_active_recovery_followup=True)
            return transport
        case NormalizedSlice1ResendAccess(input=resend_input):
            allowed = await composition.command_rate_limiter.allow(
                telegram_user_id=envelope.telegram_user_id,
                command_key=TelegramCommandRateLimitKey.ACCESS_RESEND,
            )
            if not allowed:
                await _emit_rate_limit_decision(
                    composition,
                    envelope=envelope,
                    command_key=TelegramCommandRateLimitKey.ACCESS_RESEND,
                    decision="limited",
                    correlation_id=resend_input.correlation_id,
                )
                return _rate_limited_response(resend_input.correlation_id)
            await _emit_rate_limit_decision(
                composition,
                envelope=envelope,
                command_key=TelegramCommandRateLimitKey.ACCESS_RESEND,
                decision="allowed",
                correlation_id=resend_input.correlation_id,
            )
            result = await composition.access_resend.handle(resend_input)
            return map_access_resend_to_transport(result)


class Slice1Dispatcher:
    """Thin holder for a composed slice-1 stack; delegates to :func:`dispatch_slice1_transport`."""

    __slots__ = ("_composition",)

    def __init__(self, composition: Slice1Composition) -> None:
        self._composition = composition

    async def dispatch(self, envelope: TransportIncomingEnvelope) -> TransportSafeResponse:
        return await dispatch_slice1_transport(envelope, self._composition)
