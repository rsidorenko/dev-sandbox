"""Тонкий facade runtime slice 1: сырой Telegram-подобный update → пакет отрендеренного сообщения (без SDK, без сервера).

Оркестрирует adapter → service/dispatch → storefront UI rendering / outbound keys → рендер каталога сообщений.
Сырые обновления не пересекают границу адаптера; этот модуль не принимает типы Telegram SDK.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from app.application.bootstrap import Slice1Composition
from app.application.purchase_handler import build_purchase_summary, get_available_plans
from app.bot_transport.message_catalog import RenderedMessagePackage, render_telegram_outbound_plan
from app.bot_transport.outbound import (
    build_subscription_active_recovery_confirmation_plan,
    map_transport_safe_to_outbound_plan,
)
from app.bot_transport.presentation import TransportSafeResponse
from app.bot_transport.service import handle_slice1_telegram_update
from app.bot_transport.storefront_ui import (
    CB_BALANCE,
    CB_BUY_VPN,
    CB_CONFIRM_PAY,
    CB_DEVICES,
    CB_HELP,
    CB_MAIN_MENU,
    CB_MY_KEYS,
    CB_MY_SUB,
    CB_PAY_BALANCE,
    CB_PLAN,
    CB_REFERRAL,
    CB_ROUTER,
    CB_SETTINGS,
    CB_SUB_URL,
    back_only_keyboard,
    buy_vpn_keyboard,
    confirm_pay_keyboard,
    device_select_keyboard,
    main_menu_keyboard,
    settings_keyboard,
    text_buy_vpn_intro,
    text_device_select,
    text_error_generic,
    text_help,
    text_keys_not_available,
    text_main_menu,
    text_no_subscription,
    text_payment_unavailable,
    text_purchase_summary,
    text_router_soon,
    text_settings,
    text_subscription_active,
    text_subscription_expired,
    text_welcome,
)
from app.domain.plans import get_plan
from app.security.validation import ValidationError, validate_telegram_user_id
from app.shared.types import SafeUserStatusCategory


# ─── User ID extraction ──────────────────────────────────────────────


def _extract_private_telegram_user_id(update: Mapping[str, Any]) -> int | None:
    message = update.get("message")
    if not isinstance(message, Mapping):
        return None
    chat = message.get("chat")
    if not isinstance(chat, Mapping) or chat.get("type") != "private":
        return None
    from_user = message.get("from")
    if not isinstance(from_user, Mapping):
        return None
    try:
        chat_id = validate_telegram_user_id(chat.get("id"))
        from_id = validate_telegram_user_id(from_user.get("id"))
    except (ValidationError, TypeError):
        return None
    if chat_id != from_id:
        return None
    return from_id


def _extract_user_id_from_update(update: Mapping[str, Any]) -> int | None:
    uid = _extract_private_telegram_user_id(update)
    if uid is not None:
        return uid
    cq = update.get("callback_query")
    if isinstance(cq, Mapping):
        from_user = cq.get("from")
        if isinstance(from_user, Mapping):
            try:
                return validate_telegram_user_id(from_user.get("id"))
            except (ValidationError, TypeError):
                pass
    return None


# ─── Storefront callback detection ───────────────────────────────────

_ALWAYS_STOREFRONT = frozenset({"identity_ready", "slice1_help", "store_menu"})

_CALLBACK_ONLY_STOREFRONT = frozenset({
    CB_MAIN_MENU, CB_BUY_VPN, CB_MY_SUB, CB_MY_KEYS, CB_SUB_URL,
    CB_REFERRAL, CB_BALANCE, CB_SETTINGS, CB_HELP, CB_ROUTER,
    "store_plans", "store_success", "store_success_active",
})


def _is_storefront_renderable(code: str, *, is_callback: bool) -> bool:
    if code in _ALWAYS_STOREFRONT:
        return True
    if is_callback and code in _CALLBACK_ONLY_STOREFRONT:
        return True
    if is_callback and (
        code.startswith(CB_PLAN)
        or code.startswith(CB_DEVICES)
        or code.startswith(CB_CONFIRM_PAY)
        or code.startswith(CB_PAY_BALANCE)
    ):
        return True
    return False


# ─── Storefront data helpers ─────────────────────────────────────────


async def _render_subscription_status(
    composition: Slice1Composition,
    uid: int | None,
    cid: str,
) -> tuple[str, dict[str, Any] | None]:
    if uid is None:
        return text_no_subscription(), back_only_keyboard(CB_BUY_VPN)
    from app.application.handlers import GetSubscriptionStatusInput

    result = await composition.get_status.handle(
        GetSubscriptionStatusInput(telegram_user_id=uid, correlation_id=cid),
    )
    if result.safe_status in (
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE,
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY,
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_READY,
    ):
        active_until = result.active_until_utc.date().isoformat() if result.active_until_utc else None
        return text_subscription_active(active_until, None, None), main_menu_keyboard()
    if result.safe_status == SafeUserStatusCategory.SUBSCRIPTION_EXPIRED:
        return text_subscription_expired(), back_only_keyboard(CB_BUY_VPN)
    return text_no_subscription(), back_only_keyboard(CB_BUY_VPN)


async def _has_active_subscription(
    composition: Slice1Composition,
    uid: int | None,
) -> bool:
    if uid is None:
        return False
    from app.application.handlers import GetSubscriptionStatusInput

    result = await composition.get_status.handle(
        GetSubscriptionStatusInput(telegram_user_id=uid, correlation_id="settings_check"),
    )
    return result.safe_status in (
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE,
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY,
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_READY,
    )


# ─── Storefront rendering ────────────────────────────────────────────


async def _render_storefront_response(
    transport: TransportSafeResponse,
    composition: Slice1Composition,
    update: Mapping[str, Any],
    *,
    is_callback: bool,
) -> RenderedMessagePackage | None:
    code = transport.code
    if not _is_storefront_renderable(code, is_callback=is_callback):
        return None

    cid = transport.correlation_id
    uid = _extract_user_id_from_update(update)

    text: str = text_error_generic()
    keyboard: dict[str, Any] | None = None

    if code in (CB_MAIN_MENU, "store_menu"):
        text, keyboard = text_main_menu(), main_menu_keyboard()

    elif code in (CB_BUY_VPN, "store_plans"):
        plans = get_available_plans()
        text, keyboard = text_buy_vpn_intro(), buy_vpn_keyboard(plans)

    elif code in (CB_HELP, "slice1_help"):
        text, keyboard = text_help(), back_only_keyboard(CB_MAIN_MENU)

    elif code == CB_ROUTER:
        text, keyboard = text_router_soon(), back_only_keyboard(CB_SETTINGS)

    elif code == "identity_ready":
        text, keyboard = text_welcome(), main_menu_keyboard()

    elif code in (CB_MY_SUB, "store_success", "store_success_active"):
        text, keyboard = await _render_subscription_status(composition, uid, cid)

    elif code in (CB_MY_KEYS, CB_SUB_URL):
        text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)

    elif code == CB_REFERRAL:
        if uid is not None:
            from app.application.handlers import GetSubscriptionStatusInput
            from app.application.referral_handler import get_referral_info
            identity_result = await composition.get_status.handle(
                GetSubscriptionStatusInput(telegram_user_id=uid, correlation_id=cid),
            )
            if identity_result.outcome.value == "success" and identity_result.internal_user_id is None:
                from app.application.interfaces import UserIdentityRepository
                from app.shared.types import OperationOutcomeCategory
                id_repo: UserIdentityRepository = composition.identity
                id_rec = await id_repo.find_by_telegram_user_id(uid)
                if id_rec is not None:
                    internal_uid = id_rec.internal_user_id
                else:
                    internal_uid = None
            else:
                from app.application.interfaces import UserIdentityRepository
                id_repo: UserIdentityRepository = composition.identity
                id_rec = await id_repo.find_by_telegram_user_id(uid)
                internal_uid = id_rec.internal_user_id if id_rec else None
            if internal_uid is not None and composition.bot_username:
                info = await get_referral_info(
                    internal_user_id=internal_uid,
                    code_repo=composition.referral_code_repo,
                    balance_repo=composition.referral_balance_repo,
                    relationship_repo=composition.referral_relationship_repo,
                    bot_username=composition.bot_username,
                )
                text = (
                    f"👥 Реферальная программа\n\n"
                    f"🔗 Ваша ссылка: {info.referral_link}\n"
                    f"📊 Приглашено: {info.direct_referrals_count} чел.\n"
                    f"💰 Реферальный баланс: {info.balance_rubles:.2f} ₽"
                )
            else:
                text = (
                    "👥 Реферальная программа\n\n"
                    "🔗 Ваша реферальная ссылка будет доступна после настройки.\n"
                    "💰 Реферальный баланс: 0.00 ₽"
                )
        else:
            text = (
                "👥 Реферальная программа\n\n"
                "🔗 Ваша реферальная ссылка будет доступна после настройки.\n"
                "💰 Реферальный баланс: 0.00 ₽"
            )
        keyboard = back_only_keyboard(CB_MAIN_MENU)

    elif code == CB_BALANCE:
        if uid is not None:
            from app.application.referral_handler import get_referral_balance
            from app.application.interfaces import UserIdentityRepository
            id_repo: UserIdentityRepository = composition.identity
            id_rec = await id_repo.find_by_telegram_user_id(uid)
            if id_rec is not None:
                bal = await get_referral_balance(
                    internal_user_id=id_rec.internal_user_id,
                    balance_repo=composition.referral_balance_repo,
                )
                text = f"💰 Ваш баланс: {bal.balance_rubles:.2f} ₽\n\nЭтими деньгами можно оплатить подписку."
            else:
                text = "💰 Ваш баланс: 0.00 ₽\n\nЭтими деньгами можно оплатить подписку."
        else:
            text = "💰 Ваш баланс: 0.00 ₽\n\nЭтими деньгами можно оплатить подписку."
        keyboard = back_only_keyboard(CB_MAIN_MENU)

    elif code == CB_SETTINGS:
        has_sub = await _has_active_subscription(composition, uid)
        text, keyboard = text_settings(has_sub), settings_keyboard(has_sub)

    elif code.startswith(CB_PLAN):
        plan_id = code[len(CB_PLAN):]
        plan = get_plan(plan_id)
        if plan is not None:
            text = text_device_select(plan_id, plan.price_rubles, plan.duration_months, 5)
            keyboard = device_select_keyboard(plan_id, 5)

    elif code.startswith(CB_DEVICES):
        parts = code[len(CB_DEVICES):].split(":")
        plan_id = parts[0] if parts else ""
        device_count = int(parts[1]) if len(parts) > 1 else 5
        plan = get_plan(plan_id)
        if plan is not None:
            text = text_device_select(plan_id, plan.price_rubles, plan.duration_months, device_count)
            keyboard = device_select_keyboard(plan_id, device_count)

    elif code.startswith(CB_CONFIRM_PAY):
        parts = code[len(CB_CONFIRM_PAY):].split(":")
        plan_id = parts[0] if parts else ""
        device_count = int(parts[1]) if len(parts) > 1 else 5
        summary = build_purchase_summary(plan_id=plan_id, device_count=device_count)
        if summary is not None:
            text = text_purchase_summary(summary)
            keyboard = confirm_pay_keyboard(plan_id, device_count)
        else:
            text = text_payment_unavailable()
            keyboard = back_only_keyboard(CB_BUY_VPN)

    elif code.startswith(CB_PAY_BALANCE):
        text = text_payment_unavailable()
        keyboard = back_only_keyboard(CB_MAIN_MENU)

    return RenderedMessagePackage(
        message_text=text,
        action_keys=(),
        correlation_id=cid,
        reply_markup=keyboard,
        replay_suppresses_outbound=transport.replay_suppresses_outbound,
        uc01_idempotency_key=transport.uc01_idempotency_key,
    )


# ─── Main facade function ────────────────────────────────────────────


async def handle_slice1_telegram_update_to_rendered_message(
    update: Mapping[str, Any],
    composition: Slice1Composition,
    *,
    correlation_id: str | None = None,
) -> RenderedMessagePackage:
    transport = await handle_slice1_telegram_update(
        update,
        composition,
        correlation_id=correlation_id,
    )

    is_callback = isinstance(update.get("callback_query"), Mapping)
    storefront = await _render_storefront_response(transport, composition, update, is_callback=is_callback)
    if storefront is not None:
        return storefront

    plan = map_transport_safe_to_outbound_plan(transport)
    uid = _extract_private_telegram_user_id(update)
    primary = render_telegram_outbound_plan(plan, telegram_user_id=uid)
    if not transport.subscription_active_recovery_followup:
        return primary
    confirm = render_telegram_outbound_plan(
        build_subscription_active_recovery_confirmation_plan(transport),
        telegram_user_id=uid,
    )
    return replace(primary, follow_up_messages=(confirm,))


class Slice1TelegramRuntimeFacade:
    """Вызываемая обёртка для :func:`handle_slice1_telegram_update_to_rendered_message`."""

    __slots__ = ()

    async def handle_update_to_rendered_message(
        self,
        update: Mapping[str, Any],
        composition: Slice1Composition,
        *,
        correlation_id: str | None = None,
    ) -> RenderedMessagePackage:
        return await handle_slice1_telegram_update_to_rendered_message(
            update,
            composition,
            correlation_id=correlation_id,
        )
