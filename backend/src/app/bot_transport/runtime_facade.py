"""Тонкий facade runtime slice 1: сырой Telegram-подобный update → пакет отрендеренного сообщения (без SDK, без сервера).

Оркестрирует adapter → service/dispatch → storefront UI rendering / outbound keys → рендер каталога сообщений.
Сырые обновления не пересекают границу адаптера; этот модуль не принимает типы Telegram SDK.
"""

from __future__ import annotations

import contextlib
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
    CB_ADD_DEV,
    CB_ADD_DEV_BALANCE,
    CB_ADD_DEVICE,
    CB_BALANCE,
    CB_BUY_VPN,
    CB_CONFIRM_PAY,
    CB_DEVICES,
    CB_DO_PAY,
    CB_HELP,
    CB_MAIN_MENU,
    CB_MY_KEYS,
    CB_MY_SUB,
    CB_PAY_BALANCE,
    CB_PLAN,
    CB_REFERRAL,
    CB_REMOVE_DEVICE,
    CB_ROUTER,
    CB_SETTINGS,
    CB_SUB_URL,
    add_device_confirm_keyboard,
    add_device_select_keyboard,
    back_only_keyboard,
    balance_keyboard,
    buy_vpn_keyboard,
    confirm_pay_keyboard,
    device_select_keyboard,
    main_menu_keyboard,
    no_subscription_keyboard,
    remove_device_keyboard,
    settings_keyboard,
    text_add_device_confirm,
    text_add_device_intro,
    text_add_device_success,
    text_add_device_unavailable,
    text_balance,
    text_balance_insufficient,
    text_balance_payment_success,
    text_buy_vpn_intro,
    text_device_select,
    text_error_generic,
    text_help,
    text_keys_not_available,
    text_main_menu,
    text_my_keys,
    text_no_subscription,
    text_payment_unavailable,
    text_purchase_summary,
    text_referral_program,
    text_remove_device_confirm,
    text_remove_device_success,
    text_router_soon,
    text_settings,
    text_subscription_active,
    text_subscription_expired,
    text_subscription_url,
    text_welcome,
)
from app.domain.plans import get_plan, plan_display_name
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

_CALLBACK_ONLY_STOREFRONT = frozenset(
    {
        CB_MAIN_MENU,
        CB_BUY_VPN,
        CB_MY_SUB,
        CB_MY_KEYS,
        CB_SUB_URL,
        CB_REFERRAL,
        CB_BALANCE,
        CB_SETTINGS,
        CB_HELP,
        CB_ROUTER,
        CB_ADD_DEVICE,
        CB_REMOVE_DEVICE,
        "add_device",
        "remove_device",
        "store_plans",
        "store_success",
        "store_success_active",
    }
)


def _is_storefront_renderable(code: str, *, is_callback: bool) -> bool:
    if code in _ALWAYS_STOREFRONT:
        return True
    if is_callback and code in _CALLBACK_ONLY_STOREFRONT:
        return True
    return bool(
        is_callback
        and (
            code.startswith(
                (
                    CB_PLAN,
                    CB_DEVICES,
                    CB_CONFIRM_PAY,
                    CB_PAY_BALANCE,
                    CB_DO_PAY,
                    CB_ADD_DEV_BALANCE,
                    CB_ADD_DEV,
                    "add_dev_pay:",
                    "remove_dev",
                )
            )
        )
    )


# ─── Storefront data helpers ─────────────────────────────────────────


async def _render_subscription_status(
    composition: Slice1Composition,
    uid: int | None,
    cid: str,
) -> tuple[str, dict[str, Any] | None]:
    if uid is None:
        return text_no_subscription(), no_subscription_keyboard()
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
        plan_name = None
        device_count = None
        id_rec = await composition.identity.find_by_telegram_user_id(uid)
        if id_rec is not None:
            snap = await composition.snapshots.get_for_user(id_rec.internal_user_id)
            if snap is not None:
                if snap.plan_id:
                    plan_name = plan_display_name(snap.plan_id)
                device_count = snap.device_count
        return text_subscription_active(active_until, plan_name, device_count), main_menu_keyboard()
    if result.safe_status == SafeUserStatusCategory.SUBSCRIPTION_EXPIRED:
        return text_subscription_expired(), no_subscription_keyboard()
    return text_no_subscription(), no_subscription_keyboard()


async def _has_active_subscription(
    composition: Slice1Composition,
    uid: int | None,
) -> bool:
    if uid is None:
        return False
    from app.application.handlers import GetSubscriptionStatusInput

    result = await composition.get_status.handle(
        GetSubscriptionStatusInput(telegram_user_id=uid, correlation_id="00000000000000000000000000000000"),
    )
    return result.safe_status in (
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE,
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY,
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_READY,
    )


# ─── Balance payment helpers ──────────────────────────────────────────


async def _get_internal_user_id(
    composition: Slice1Composition,
    uid: int | None,
) -> str | None:
    if uid is None:
        return None
    id_rec = await composition.identity.find_by_telegram_user_id(uid)
    return id_rec.internal_user_id if id_rec is not None else None


async def _process_balance_payment(
    composition: Slice1Composition,
    uid: int | None,
    *,
    plan_id: str,
    device_count: int,
) -> tuple[str, dict[str, Any] | None]:
    import calendar
    import uuid
    from datetime import UTC, datetime

    from app.application.interfaces import SubscriptionSnapshot
    from app.domain.plans import calculate_total_price_kopecks
    from app.persistence.referral_contracts import ReferralTransactionRecord

    plan = get_plan(plan_id)
    if plan is None:
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)

    internal_user_id = await _get_internal_user_id(composition, uid)
    if internal_user_id is None:
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    total_kopecks = calculate_total_price_kopecks(plan, device_count)

    balance_record = await composition.referral_balance_repo.get_balance(internal_user_id)
    balance_kopecks = balance_record.balance_kopecks if balance_record else 0

    if balance_kopecks < total_kopecks:
        return text_balance_insufficient(), back_only_keyboard(CB_BUY_VPN)

    debit_result = await composition.referral_balance_repo.debit(internal_user_id, total_kopecks)
    if debit_result is None:
        return text_balance_insufficient(), back_only_keyboard(CB_BUY_VPN)

    now = datetime.now(UTC)
    existing_snap = await composition.snapshots.get_for_user(internal_user_id)
    base_date = now
    if (
        existing_snap is not None
        and existing_snap.active_until_utc is not None
        and existing_snap.active_until_utc > now
    ):
        base_date = existing_snap.active_until_utc
    new_month = base_date.month + plan.duration_months
    new_year = base_date.year + (new_month - 1) // 12
    new_month = ((new_month - 1) % 12) + 1
    max_day = calendar.monthrange(new_year, new_month)[1]
    active_until = base_date.replace(
        year=new_year,
        month=new_month,
        day=min(base_date.day, max_day),
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    await composition.snapshots.upsert_state(
        SubscriptionSnapshot(
            internal_user_id=internal_user_id,
            state_label="active",
            active_until_utc=active_until,
            plan_id=plan_id,
            device_count=device_count,
        )
    )

    if composition.vless_provider is not None:
        with contextlib.suppress(Exception):
            await composition.vless_provider.create_user(internal_user_id=internal_user_id)

    correlation_id = f"bal-pay-{uuid.uuid4()}"
    await composition.referral_transaction_repo.append_transaction(
        ReferralTransactionRecord(
            transaction_id=correlation_id,
            internal_user_id=internal_user_id,
            amount_kopecks=total_kopecks,
            transaction_type="subscription_payment",
            related_user_id=None,
            related_plan_id=plan_id,
            description=f"balance payment: {plan_id} x {device_count} devices",
            created_at=now,
        )
    )

    await _credit_referral_commissions(
        composition=composition,
        payer_internal_user_id=internal_user_id,
        payment_amount_kopecks=total_kopecks,
        plan_id=plan_id,
        correlation_prefix="bal",
    )

    active_until_str = active_until.date().isoformat()
    return text_balance_payment_success(active_until_str), main_menu_keyboard()


async def _credit_referral_commissions(
    *,
    composition: Slice1Composition,
    payer_internal_user_id: str,
    payment_amount_kopecks: int,
    plan_id: str,
    correlation_prefix: str,
) -> None:
    import uuid
    from datetime import UTC, datetime

    from app.domain.referral import build_commissions_for_payment
    from app.persistence.referral_contracts import ReferralTransactionRecord

    referrers = await composition.referral_relationship_repo.find_referrers(payer_internal_user_id)
    direct_referrer = None
    indirect_referrer = None
    for r in referrers:
        if r.level == 1:
            direct_referrer = r.referrer_user_id
        if r.level == 2:
            indirect_referrer = r.referrer_user_id

    commissions = build_commissions_for_payment(
        payer_user_id=payer_internal_user_id,
        direct_referrer_user_id=direct_referrer,
        indirect_referrer_user_id=indirect_referrer,
        plan_id=plan_id,
        payment_amount_kopecks=payment_amount_kopecks,
    )
    for comm in commissions:
        dedup_desc = f"{correlation_prefix}:l{comm.level}:{comm.payer_user_id}:{comm.plan_id}"
        existing = await composition.referral_transaction_repo.list_by_user(comm.referrer_user_id, limit=100)
        if any(t.description == dedup_desc for t in existing):
            continue
        await composition.referral_balance_repo.credit(comm.referrer_user_id, comm.amount_kopecks)
        await composition.referral_transaction_repo.append_transaction(
            ReferralTransactionRecord(
                transaction_id=f"ref-{uuid.uuid4()}",
                internal_user_id=comm.referrer_user_id,
                amount_kopecks=comm.amount_kopecks,
                transaction_type="referral_credit",
                related_user_id=comm.payer_user_id,
                related_plan_id=comm.plan_id,
                description=dedup_desc,
                created_at=datetime.now(UTC),
            )
        )


# ─── Add device helpers ────────────────────────────────────────────────


async def _render_add_device_intro(
    composition: Slice1Composition,
    uid: int | None,
) -> tuple[str, dict[str, Any] | None]:
    if uid is None:
        return text_add_device_unavailable(), back_only_keyboard(CB_MAIN_MENU)

    internal_user_id = await _get_internal_user_id(composition, uid)
    if internal_user_id is None:
        return text_add_device_unavailable(), back_only_keyboard(CB_MAIN_MENU)

    has_sub = await _has_active_subscription(composition, uid)
    if not has_sub:
        return text_add_device_unavailable(), back_only_keyboard(CB_MAIN_MENU)

    snap = await composition.snapshots.get_for_user(internal_user_id)
    current = snap.device_count if snap and snap.device_count else 5

    text = text_add_device_intro(current)
    return text, add_device_select_keyboard(current)


async def _render_add_device_confirm(
    composition: Slice1Composition,
    uid: int | None,
    new_count: int,
) -> tuple[str, dict[str, Any] | None]:
    if uid is None:
        return text_add_device_unavailable(), back_only_keyboard(CB_SETTINGS)

    internal_user_id = await _get_internal_user_id(composition, uid)
    if internal_user_id is None:
        return text_add_device_unavailable(), back_only_keyboard(CB_SETTINGS)

    snap = await composition.snapshots.get_for_user(internal_user_id)
    current = snap.device_count if snap and snap.device_count else 5

    if new_count <= current:
        return text_add_device_intro(current), add_device_select_keyboard(current)

    extra = new_count - current
    cost = extra * 80

    balance_record = await composition.referral_balance_repo.get_balance(internal_user_id)
    balance_kopecks = balance_record.balance_kopecks if balance_record else 0

    text = text_add_device_confirm(current, new_count)
    return text, add_device_confirm_keyboard(
        new_count,
        balance_kopecks=balance_kopecks,
        cost_kopecks=cost * 100,
    )


async def _process_add_device_balance(
    composition: Slice1Composition,
    uid: int | None,
    new_count: int,
) -> tuple[str, dict[str, Any] | None]:
    import uuid
    from datetime import UTC, datetime

    from app.application.interfaces import SubscriptionSnapshot
    from app.persistence.referral_contracts import ReferralTransactionRecord

    if uid is None:
        return text_error_generic(), back_only_keyboard(CB_SETTINGS)

    internal_user_id = await _get_internal_user_id(composition, uid)
    if internal_user_id is None:
        return text_error_generic(), back_only_keyboard(CB_SETTINGS)

    snap = await composition.snapshots.get_for_user(internal_user_id)
    if snap is None:
        return text_add_device_unavailable(), back_only_keyboard(CB_MAIN_MENU)

    current = snap.device_count or 5
    if new_count <= current:
        return text_add_device_intro(current), add_device_select_keyboard(current)

    extra = new_count - current
    cost_kopecks = extra * 80 * 100

    debit_result = await composition.referral_balance_repo.debit(internal_user_id, cost_kopecks)
    if debit_result is None:
        return text_balance_insufficient(), back_only_keyboard(CB_SETTINGS)

    now = datetime.now(UTC)
    await composition.referral_transaction_repo.append_transaction(
        ReferralTransactionRecord(
            transaction_id=f"add-dev-{uuid.uuid4()}",
            internal_user_id=internal_user_id,
            amount_kopecks=cost_kopecks,
            transaction_type="subscription_payment",
            related_user_id=None,
            related_plan_id=snap.plan_id,
            description=f"add device: {current} → {new_count}",
            created_at=now,
        )
    )

    await composition.snapshots.upsert_state(
        SubscriptionSnapshot(
            internal_user_id=internal_user_id,
            state_label=snap.state_label,
            active_until_utc=snap.active_until_utc,
            plan_id=snap.plan_id,
            device_count=new_count,
        )
    )

    return text_add_device_success(new_count), back_only_keyboard(CB_MAIN_MENU)


# ─── Remove device helpers ────────────────────────────────────────────


async def _render_remove_device(
    composition: Slice1Composition,
    uid: int | None,
) -> tuple[str, dict[str, Any] | None]:
    if uid is None:
        return text_add_device_unavailable(), back_only_keyboard(CB_MAIN_MENU)

    internal_user_id = await _get_internal_user_id(composition, uid)
    if internal_user_id is None:
        return text_add_device_unavailable(), back_only_keyboard(CB_MAIN_MENU)

    has_sub = await _has_active_subscription(composition, uid)
    if not has_sub:
        return text_add_device_unavailable(), back_only_keyboard(CB_MAIN_MENU)

    snap = await composition.snapshots.get_for_user(internal_user_id)
    current = snap.device_count if snap and snap.device_count else 5

    if current <= 5:
        plan_name = plan_display_name(snap.plan_id) if snap and snap.plan_id else None
        active_until = snap.active_until_utc.date().isoformat() if snap and snap.active_until_utc else None
        return text_settings(
            True, plan_name=plan_name, device_count=current, active_until=active_until
        ), settings_keyboard(True, current)

    new_count = max(5, current - 1)
    text = text_remove_device_confirm(current, new_count)
    return text, remove_device_keyboard(current)


async def _process_remove_device(
    composition: Slice1Composition,
    uid: int | None,
    new_count: int,
) -> tuple[str, dict[str, Any] | None]:
    from app.application.interfaces import SubscriptionSnapshot

    if uid is None:
        return text_error_generic(), back_only_keyboard(CB_SETTINGS)

    internal_user_id = await _get_internal_user_id(composition, uid)
    if internal_user_id is None:
        return text_error_generic(), back_only_keyboard(CB_SETTINGS)

    snap = await composition.snapshots.get_for_user(internal_user_id)
    if snap is None:
        return text_add_device_unavailable(), back_only_keyboard(CB_MAIN_MENU)

    current = snap.device_count or 5
    if new_count >= current or new_count < 5:
        plan_name = plan_display_name(snap.plan_id) if snap.plan_id else None
        active_until = snap.active_until_utc.date().isoformat() if snap.active_until_utc else None
        return text_settings(
            True, plan_name=plan_name, device_count=current, active_until=active_until
        ), settings_keyboard(True, current)

    await composition.snapshots.upsert_state(
        SubscriptionSnapshot(
            internal_user_id=internal_user_id,
            state_label=snap.state_label,
            active_until_utc=snap.active_until_utc,
            plan_id=snap.plan_id,
            device_count=new_count,
        )
    )

    return text_remove_device_success(new_count), back_only_keyboard(CB_MAIN_MENU)


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
        has_sub = await _has_active_subscription(composition, uid)
        if has_sub and uid is not None and composition.vless_provider is not None:
            from app.issuance.vless_provider import VlessProviderOutcome

            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                vless_result = await composition.vless_provider.get_user_config(
                    internal_user_id=id_rec.internal_user_id,
                )
                if vless_result.outcome == VlessProviderOutcome.SUCCESS and vless_result.config is not None:
                    if code == CB_MY_KEYS:
                        text, keyboard = text_my_keys(vless_result.config), back_only_keyboard(CB_MAIN_MENU)
                    else:
                        text, keyboard = text_subscription_url(vless_result.config), back_only_keyboard(CB_MAIN_MENU)
                else:
                    text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)
            else:
                text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)
        else:
            text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)

    elif code == CB_REFERRAL:
        if uid is not None and composition.bot_username:
            from app.application.referral_handler import ReferralInfo, get_referral_info

            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                info = await get_referral_info(
                    internal_user_id=id_rec.internal_user_id,
                    code_repo=composition.referral_code_repo,
                    balance_repo=composition.referral_balance_repo,
                    relationship_repo=composition.referral_relationship_repo,
                    bot_username=composition.bot_username,
                )
                text, keyboard = text_referral_program(info), back_only_keyboard(CB_MAIN_MENU)
            else:
                text, keyboard = (
                    text_referral_program(
                        ReferralInfo(referral_code="", referral_link="", balance_rubles=0.0, direct_referrals_count=0)
                    ),
                    back_only_keyboard(CB_MAIN_MENU),
                )
        else:
            from app.application.referral_handler import ReferralInfo

            text, keyboard = (
                text_referral_program(
                    ReferralInfo(referral_code="", referral_link="", balance_rubles=0.0, direct_referrals_count=0)
                ),
                back_only_keyboard(CB_MAIN_MENU),
            )

    elif code == CB_BALANCE:
        if uid is not None:
            from app.application.referral_handler import ReferralBalanceInfo, get_referral_balance

            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                bal = await get_referral_balance(
                    internal_user_id=id_rec.internal_user_id,
                    balance_repo=composition.referral_balance_repo,
                )
                text, keyboard = text_balance(bal), balance_keyboard()
            else:
                text, keyboard = (
                    text_balance(ReferralBalanceInfo(balance_rubles=0.0, balance_kopecks=0)),
                    balance_keyboard(),
                )
        else:
            from app.application.referral_handler import ReferralBalanceInfo

            text, keyboard = (
                text_balance(ReferralBalanceInfo(balance_rubles=0.0, balance_kopecks=0)),
                balance_keyboard(),
            )

    elif code == CB_SETTINGS:
        has_sub = await _has_active_subscription(composition, uid)
        device_count = None
        plan_name = None
        active_until = None
        if has_sub and uid is not None:
            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                snap = await composition.snapshots.get_for_user(id_rec.internal_user_id)
                if snap is not None:
                    device_count = snap.device_count
                    if snap.plan_id:
                        plan_name = plan_display_name(snap.plan_id)
                    if snap.active_until_utc is not None:
                        active_until = snap.active_until_utc.date().isoformat()
        text, keyboard = (
            text_settings(
                has_sub,
                plan_name=plan_name,
                device_count=device_count,
                active_until=active_until,
            ),
            settings_keyboard(has_sub, device_count),
        )

    elif code.startswith(CB_PLAN):
        plan_id = code[len(CB_PLAN) :]
        plan = get_plan(plan_id)
        if plan is not None:
            text = text_device_select(plan_id, plan.price_rubles, plan.duration_months, 5)
            keyboard = device_select_keyboard(plan_id, 5)

    elif code.startswith(CB_DEVICES):
        parts = code[len(CB_DEVICES) :].split(":")
        plan_id = parts[0] if parts else ""
        device_count = int(parts[1]) if len(parts) > 1 else 5
        plan = get_plan(plan_id)
        if plan is not None:
            text = text_device_select(plan_id, plan.price_rubles, plan.duration_months, device_count)
            keyboard = device_select_keyboard(plan_id, device_count)

    elif code.startswith(CB_CONFIRM_PAY):
        parts = code[len(CB_CONFIRM_PAY) :].split(":")
        plan_id = parts[0] if parts else ""
        device_count = int(parts[1]) if len(parts) > 1 else 5
        summary = build_purchase_summary(plan_id=plan_id, device_count=device_count)
        if summary is not None:
            user_balance_kopecks = 0
            if uid is not None:
                id_rec = await composition.identity.find_by_telegram_user_id(uid)
                if id_rec is not None:
                    from app.application.referral_handler import get_referral_balance

                    bal = await get_referral_balance(
                        internal_user_id=id_rec.internal_user_id,
                        balance_repo=composition.referral_balance_repo,
                    )
                    user_balance_kopecks = bal.balance_kopecks
            text = text_purchase_summary(summary)
            keyboard = confirm_pay_keyboard(
                plan_id,
                device_count,
                balance_kopecks=user_balance_kopecks,
                total_kopecks=summary.total_price_rubles * 100,
            )
        else:
            text = text_payment_unavailable()
            keyboard = back_only_keyboard(CB_BUY_VPN)

    elif code.startswith(CB_DO_PAY):
        text = text_payment_unavailable()
        keyboard = back_only_keyboard(CB_BUY_VPN)

    elif code.startswith(CB_PAY_BALANCE):
        parts = code[len(CB_PAY_BALANCE) :].split(":")
        plan_id = parts[0] if parts else ""
        device_count = int(parts[1]) if len(parts) > 1 else 5
        text, keyboard = await _process_balance_payment(
            composition,
            uid,
            plan_id=plan_id,
            device_count=device_count,
        )

    elif code in (CB_ADD_DEVICE, "add_device"):
        text, keyboard = await _render_add_device_intro(composition, uid)

    elif code.startswith(CB_ADD_DEV_BALANCE):
        new_count_str = code[len(CB_ADD_DEV_BALANCE) :]
        try:
            new_count = int(new_count_str)
            text, keyboard = await _process_add_device_balance(composition, uid, new_count)
        except (ValueError, TypeError):
            text = text_error_generic()
            keyboard = back_only_keyboard(CB_SETTINGS)

    elif code.startswith(CB_ADD_DEV):
        remainder = code[len(CB_ADD_DEV) :]
        if remainder.startswith("confirm:"):
            new_count = int(remainder.split(":")[1])
            text, keyboard = await _render_add_device_confirm(composition, uid, new_count)
        elif remainder.startswith("pay:"):
            text = text_payment_unavailable()
            keyboard = back_only_keyboard(CB_SETTINGS)
        else:
            try:
                new_count = int(remainder)
                text = text_add_device_intro(new_count)
                keyboard = add_device_select_keyboard(new_count)
            except (ValueError, TypeError):
                text = text_error_generic()
                keyboard = back_only_keyboard(CB_SETTINGS)

    elif code.startswith("add_dev_pay:"):
        text = text_payment_unavailable()
        keyboard = back_only_keyboard(CB_SETTINGS)

    elif code in (CB_REMOVE_DEVICE, "remove_device"):
        text, keyboard = await _render_remove_device(composition, uid)

    elif code.startswith("remove_dev_confirm:"):
        new_count_str = code[len("remove_dev_confirm:") :]
        try:
            new_count = int(new_count_str)
            text, keyboard = await _process_remove_device(composition, uid, new_count)
        except (ValueError, TypeError):
            text = text_error_generic()
            keyboard = back_only_keyboard(CB_SETTINGS)

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
