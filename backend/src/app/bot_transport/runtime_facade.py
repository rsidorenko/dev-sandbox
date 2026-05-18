"""Тонкий facade runtime slice 1: сырой Telegram-подобный update → пакет отрендеренного сообщения (без SDK, без сервера).

Оркестрирует adapter → service/dispatch → storefront UI rendering / outbound keys → рендер каталога сообщений.
Сырые обновления не пересекают границу адаптера; этот модуль не принимает типы Telegram SDK.
"""

from __future__ import annotations

import contextlib
import uuid
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
    CB_LINK_EMAIL,
    CB_MAIN_MENU,
    CB_RESEND_EMAIL_CODE,
    CB_MY_KEYS,
    CB_REISSUE_KEYS,
    CB_REISSUE_CONFIRM,
    CB_CONNECT_DEVICE,
    CB_CONNECT_WIN,
    CB_CONNECT_ANDROID,
    CB_CONNECT_IOS,
    CB_CONNECT_MAC,
    CB_CONNECT_NEXT,
    CB_CONNECT_DONE,
    CB_MY_SUB,
    CB_PAY_BALANCE,
    CB_PLAN,
    CB_REFERRAL,
    CB_REMOVE_DEVICE,
    CB_ROUTER,
    CB_SETTINGS,
    CB_TRIAL,
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
    text_link_email_already_linked,
    link_email_code_keyboard,
    text_link_email_code_sent,
    text_link_email_error,
    text_link_email_intro,
    link_email_keyboard,
    text_link_email_success,
    text_main_menu,
    text_my_keys,
    text_keys_not_available,
    keys_keyboard,
    text_reissue_confirm,
    reissue_confirm_keyboard,
    text_connect_device,
    connect_device_keyboard,
    text_connect_platform,
    connect_platform_keyboard,
    text_connect_config,
    connect_config_keyboard,
    text_connect_done,
    connect_done_keyboard,
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
    text_trial_activated,
    text_trial_offer,
    trial_activated_keyboard,
    trial_offer_keyboard,
    text_welcome,
    welcome_keyboard,
)
from app.domain.devices import DEFAULT_DEVICE_LIMIT as DEVICES_DEFAULT
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
        CB_CONNECT_DEVICE,
        CB_CONNECT_WIN,
        CB_CONNECT_ANDROID,
        CB_CONNECT_IOS,
        CB_CONNECT_MAC,
        CB_CONNECT_NEXT,
        CB_CONNECT_DONE,
        CB_REFERRAL,
        CB_BALANCE,
        CB_SETTINGS,
        CB_HELP,
        CB_ROUTER,
        CB_TRIAL,
        CB_ADD_DEVICE,
        CB_REMOVE_DEVICE,
        CB_LINK_EMAIL,
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


# ─── Trial period helpers ──────────────────────────────────────────────


async def _is_trial_available(
    composition: Slice1Composition,
    uid: int | None,
) -> bool:
    """Check if the user can still activate a free trial."""
    if uid is None:
        return False
    id_rec = await composition.identity.find_by_telegram_user_id(uid)
    if id_rec is None:
        return False
    snap = await composition.snapshots.get_for_user(id_rec.internal_user_id)
    # Trial available if: never used trial AND no active subscription
    if snap is not None and snap.state_label == "active":
        return False
    if snap is not None and snap.trial_started_at is not None:
        return False
    # Check trial_used flag on identity
    pool = _get_pool_from_composition(composition)
    if pool is not None:
        row = await pool.fetchrow(
            "SELECT trial_used FROM user_identities WHERE internal_user_id = $1",
            id_rec.internal_user_id,
        )
        if row is not None and row["trial_used"]:
            return False
    return True


async def _handle_trial_activation(
    composition: Slice1Composition,
    uid: int | None,
) -> tuple[str, dict[str, Any] | None]:
    """Activate 3-day trial: create VLESS keys, set trial dates."""
    from datetime import UTC, datetime

    from app.application.interfaces import SubscriptionSnapshot
    from app.domain.trial import trial_expires_at

    if uid is None:
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    id_rec = await composition.identity.find_by_telegram_user_id(uid)
    if id_rec is None:
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    # Check trial not already used
    snap = await composition.snapshots.get_for_user(id_rec.internal_user_id)
    if snap is not None and snap.trial_started_at is not None:
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    pool = _get_pool_from_composition(composition)
    if pool is not None:
        row = await pool.fetchrow(
            "SELECT trial_used FROM user_identities WHERE internal_user_id = $1",
            id_rec.internal_user_id,
        )
        if row is not None and row["trial_used"]:
            return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    # Check VLESS provider available
    if composition.vless_provider is None:
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    # Create VLESS user
    from app.issuance.vless_provider import VlessProviderOutcome

    vless_result = await composition.vless_provider.create_user(
        internal_user_id=id_rec.internal_user_id,
    )
    if vless_result.outcome != VlessProviderOutcome.SUCCESS or vless_result.config is None:
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    # Set trial period
    now = datetime.now(UTC)
    expires = trial_expires_at(now)

    await composition.snapshots.upsert_state(
        SubscriptionSnapshot(
            internal_user_id=id_rec.internal_user_id,
            state_label="active",
            active_until_utc=expires,
            trial_started_at=now,
            trial_expires_at=expires,
        )
    )

    # Mark trial as used
    if pool is not None:
        await pool.execute(
            "UPDATE user_identities SET trial_used = TRUE WHERE internal_user_id = $1",
            id_rec.internal_user_id,
        )

    return text_trial_activated(vless_result.config), trial_activated_keyboard()


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
        GetSubscriptionStatusInput(telegram_user_id=uid, correlation_id=uuid.uuid4().hex),
    )
    return result.safe_status in (
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE,
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY,
        SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_READY,
    )


# ─── Balance payment helpers ──────────────────────────────────────────


def _get_pool_from_composition(composition: Slice1Composition):
    """Extract asyncpg pool from composition's postgres identity repository."""
    repo = composition.identity
    pool = getattr(repo, "_pool", None)
    if pool is not None:
        return pool
    return None


async def _get_linked_email(
    composition: Slice1Composition,
    uid: int | None,
) -> str | None:
    """Return verified email for a telegram user, if any."""
    if uid is None:
        return None

    pool = _get_pool_from_composition(composition)
    if pool is None:
        return None
    row = await pool.fetchrow(
        "SELECT email FROM user_emails WHERE telegram_user_id = $1 AND is_verified = TRUE LIMIT 1",
        uid,
    )
    return row["email"] if row else None


async def _handle_email_linking(
    composition: Slice1Composition,
    uid: int | None,
    user_text: str,
) -> tuple[str, dict[str, Any] | None] | None:
    """Handle email input or verification code during email linking flow.

    Returns (text, keyboard) if handled, None otherwise.
    """
    if uid is None:
        return None

    pool = _get_pool_from_composition(composition)
    if pool is None:
        return None

    text = user_text.strip()

    # Check if there's a pending email verification for this user
    pending = await pool.fetchrow(
        """SELECT v.email, v.expires_at
           FROM email_verification_codes v
           WHERE v.telegram_user_id = $1 AND v.purpose = 'link_email' AND v.used_at IS NULL
           ORDER BY v.created_at DESC LIMIT 1""",
        uid,
    )

    if pending is not None:
        # User is in code verification phase — treat text as code
        from datetime import UTC, datetime

        if pending["expires_at"] < datetime.now(UTC):
            return text_link_email_error("code_expired"), link_email_keyboard()

        # Try to verify via internal API logic
        from app.web_api.email_link import _hash_code

        code_hash = _hash_code(text)
        row = await pool.fetchrow(
            """SELECT id, code_hash, attempts, max_attempts
               FROM email_verification_codes
               WHERE email = $1 AND purpose = 'link_email' AND telegram_user_id = $2 AND used_at IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            pending["email"],
            uid,
        )
        if row is None:
            return text_link_email_error("invalid_code"), link_email_keyboard()

        await pool.execute(
            "UPDATE email_verification_codes SET attempts = attempts + 1 WHERE id = $1",
            row["id"],
        )

        import hmac as hmac_mod

        if row["attempts"] >= row["max_attempts"]:
            return text_link_email_error("too_many_attempts"), link_email_keyboard()

        if not hmac_mod.compare_digest(row["code_hash"], code_hash):
            return text_link_email_error("invalid_code"), link_email_code_keyboard()

        # Code is correct — link email
        now = datetime.now(UTC)
        await pool.execute(
            "UPDATE email_verification_codes SET used_at = $1 WHERE id = $2",
            now,
            row["id"],
        )
        await pool.execute(
            "UPDATE user_emails SET is_verified = FALSE WHERE telegram_user_id = $1 AND is_verified = TRUE",
            uid,
        )
        await pool.execute(
            """INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at)
               VALUES ($1, $2, TRUE, $3)
               ON CONFLICT (telegram_user_id, email) DO UPDATE SET is_verified = TRUE, verified_at = $3""",
            uid,
            pending["email"],
            now,
        )

        # Merge web-only account if this email was previously registered on the website
        from app.persistence.account_merge import merge_web_account_if_needed
        await merge_web_account_if_needed(pool, uid, pending["email"])

        return text_link_email_success(pending["email"]), main_menu_keyboard()

    # No pending verification — treat text as email
    if "@" not in text or "." not in text.split("@")[-1]:
        return None  # Not an email, let normal processing handle it

    email = text.lower().strip()

    # Check if already linked
    existing = await pool.fetchrow(
        "SELECT telegram_user_id FROM user_emails WHERE email = $1 AND is_verified = TRUE",
        email,
    )
    if existing and existing["telegram_user_id"] == uid:
        return text_link_email_already_linked(email), main_menu_keyboard()
    if existing:
        return text_link_email_error("email_belongs_to_other_account"), link_email_keyboard()

    # Send verification code
    from datetime import UTC, datetime, timedelta

    from app.web_api.email_link import _generate_code, _hash_code as _hash, _MAX_SEND_PER_EMAIL_PER_HOUR

    recent = await pool.fetchval(
        """SELECT COUNT(*) FROM email_verification_codes
           WHERE email = $1 AND created_at > NOW() - INTERVAL '1 hour'""",
        email,
    )
    if recent is not None and recent >= _MAX_SEND_PER_EMAIL_PER_HOUR:
        return text_link_email_error("rate_limited"), link_email_keyboard()

    code = _generate_code()
    code_hash = _hash(code)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)

    await pool.execute(
        """INSERT INTO email_verification_codes (email, code_hash, purpose, telegram_user_id, expires_at)
           VALUES ($1, $2, 'link_email', $3, $4)""",
        email,
        code_hash,
        uid,
        expires_at,
    )

    from app.email.sender import send_verification_code

    sent = await send_verification_code(email, code)
    if not sent:
        return text_link_email_error("smtp_not_configured"), link_email_keyboard()

    return text_link_email_code_sent(email), link_email_code_keyboard()


async def _handle_resend_email_code(
    composition: Slice1Composition,
    uid: int | None,
) -> tuple[str, dict[str, Any] | None]:
    if uid is None:
        return text_link_email_intro(), link_email_keyboard()

    pool = _get_pool_from_composition(composition)
    if pool is None:
        return text_link_email_intro(), link_email_keyboard()

    # Find the most recent pending email for this user
    pending = await pool.fetchrow(
        """SELECT email FROM email_verification_codes
           WHERE telegram_user_id = $1 AND purpose = 'link_email' AND used_at IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        uid,
    )
    if pending is None:
        return text_link_email_intro(), link_email_keyboard()

    email = pending["email"]

    # Invalidate old unused codes for this email+user
    from datetime import UTC, datetime

    await pool.execute(
        """UPDATE email_verification_codes SET used_at = $1
           WHERE email = $2 AND purpose = 'link_email' AND telegram_user_id = $3 AND used_at IS NULL""",
        datetime.now(UTC),
        email,
        uid,
    )

    # Rate limit check
    from app.web_api.email_link import _MAX_SEND_PER_EMAIL_PER_HOUR

    recent = await pool.fetchval(
        """SELECT COUNT(*) FROM email_verification_codes
           WHERE email = $1 AND created_at > NOW() - INTERVAL '1 hour'""",
        email,
    )
    if recent is not None and recent >= _MAX_SEND_PER_EMAIL_PER_HOUR:
        return text_link_email_error("rate_limited"), link_email_code_keyboard()

    # Generate and send new code
    from datetime import timedelta

    from app.web_api.email_link import _generate_code, _hash_code as _hash

    code = _generate_code()
    code_hash = _hash(code)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)

    await pool.execute(
        """INSERT INTO email_verification_codes (email, code_hash, purpose, telegram_user_id, expires_at)
           VALUES ($1, $2, 'link_email', $3, $4)""",
        email,
        code_hash,
        uid,
        expires_at,
    )

    from app.email.sender import send_verification_code

    sent = await send_verification_code(email, code)
    if not sent:
        return text_link_email_error("smtp_not_configured"), link_email_code_keyboard()

    return text_link_email_code_sent(email), link_email_code_keyboard()


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
    try:
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
    except Exception:
        # Компенсирующая транзакция: вернуть списанные средства при ошибке активации
        with contextlib.suppress(Exception):
            await composition.referral_balance_repo.credit(internal_user_id, total_kopecks)
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    if composition.vless_provider is not None:
        with contextlib.suppress(Exception):
            pool = _get_pool_from_composition(composition)
            if pool is not None:
                snap_check = await pool.fetchrow(
                    """SELECT keys_deactivated_at, keys_deleted_at FROM subscription_snapshots
                       WHERE internal_user_id = $1""",
                    internal_user_id,
                )
                if snap_check is not None and snap_check["keys_deleted_at"] is not None:
                    # Keys were deleted — create new ones
                    await composition.vless_provider.create_user(internal_user_id=internal_user_id)
                elif snap_check is not None and snap_check["keys_deactivated_at"] is not None:
                    # Keys were deactivated — re-enable
                    await composition.vless_provider.activate_user(internal_user_id=internal_user_id)
                else:
                    await composition.vless_provider.create_user(internal_user_id=internal_user_id)
            else:
                await composition.vless_provider.create_user(internal_user_id=internal_user_id)
        # Reset lifecycle tracking
        if pool is not None:
            with contextlib.suppress(Exception):
                await pool.execute(
                    """UPDATE subscription_snapshots
                       SET keys_deactivated_at = NULL, keys_deleted_at = NULL, updated_at = NOW()
                       WHERE internal_user_id = $1""",
                    internal_user_id,
                )

    correlation_id = f"bal-pay-{uuid.uuid4()}"
    await composition.referral_transaction_repo.append_transaction(
        ReferralTransactionRecord(
            transaction_id=correlation_id,
            internal_user_id=internal_user_id,
            amount_kopecks=-total_kopecks,
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

    from app.domain.referral import build_commissions_for_payment, resolve_direct_and_indirect_referrers
    from app.persistence.referral_contracts import ReferralTransactionRecord

    referrers = await composition.referral_relationship_repo.find_referrers(payer_internal_user_id)
    direct_referrer, indirect_referrer = resolve_direct_and_indirect_referrers(referrers)

    commissions = build_commissions_for_payment(
        payer_user_id=payer_internal_user_id,
        direct_referrer_user_id=direct_referrer,
        indirect_referrer_user_id=indirect_referrer,
        plan_id=plan_id,
        payment_amount_kopecks=payment_amount_kopecks,
    )
    for comm in commissions:
        dedup_desc = f"{correlation_prefix}:l{comm.level}:{comm.payer_user_id}:{comm.plan_id}:{payment_amount_kopecks}"
        tx_record = ReferralTransactionRecord(
            transaction_id=f"ref-{uuid.uuid4()}",
            internal_user_id=comm.referrer_user_id,
            amount_kopecks=comm.amount_kopecks,
            transaction_type="referral_credit",
            related_user_id=comm.payer_user_id,
            related_plan_id=comm.plan_id,
            description=dedup_desc,
            created_at=datetime.now(UTC),
        )
        inserted = await composition.referral_transaction_repo.append_transaction_if_description_absent(tx_record)
        if inserted:
            await composition.referral_balance_repo.credit(comm.referrer_user_id, comm.amount_kopecks)


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
    current = snap.device_count if snap and snap.device_count else DEVICES_DEFAULT

    text = text_add_device_intro(current)
    return text, add_device_select_keyboard(current)


async def _render_add_device_confirm(
    composition: Slice1Composition,
    uid: int | None,
    new_count: int,
) -> tuple[str, dict[str, Any] | None]:
    from app.domain.devices import extra_device_cost

    if uid is None:
        return text_add_device_unavailable(), back_only_keyboard(CB_SETTINGS)

    internal_user_id = await _get_internal_user_id(composition, uid)
    if internal_user_id is None:
        return text_add_device_unavailable(), back_only_keyboard(CB_SETTINGS)

    snap = await composition.snapshots.get_for_user(internal_user_id)
    current = snap.device_count if snap and snap.device_count else DEVICES_DEFAULT

    if new_count <= current:
        return text_add_device_intro(current), add_device_select_keyboard(current)

    duration_months = 1
    if snap and snap.plan_id:
        plan = get_plan(snap.plan_id)
        if plan is not None:
            duration_months = plan.duration_months

    cost = extra_device_cost(new_count, current, duration_months)

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

    current = snap.device_count or DEVICES_DEFAULT
    if new_count <= current:
        return text_add_device_intro(current), add_device_select_keyboard(current)

    from app.domain.devices import extra_device_cost as _extra_device_cost

    duration_months = 1
    if snap.plan_id:
        plan = get_plan(snap.plan_id)
        if plan is not None:
            duration_months = plan.duration_months

    cost_rubles = _extra_device_cost(new_count, current, duration_months)
    cost_kopecks = cost_rubles * 100

    debit_result = await composition.referral_balance_repo.debit(internal_user_id, cost_kopecks)
    if debit_result is None:
        return text_balance_insufficient(), back_only_keyboard(CB_SETTINGS)

    now = datetime.now(UTC)
    try:
        await composition.snapshots.upsert_state(
            SubscriptionSnapshot(
                internal_user_id=internal_user_id,
                state_label=snap.state_label,
                active_until_utc=snap.active_until_utc,
                plan_id=snap.plan_id,
                device_count=new_count,
            )
        )
    except Exception:
        # Компенсирующая транзакция: вернуть списанные средства при ошибке обновления
        with contextlib.suppress(Exception):
            await composition.referral_balance_repo.credit(internal_user_id, cost_kopecks)
        return text_error_generic(), back_only_keyboard(CB_SETTINGS)

    await composition.referral_transaction_repo.append_transaction(
        ReferralTransactionRecord(
            transaction_id=f"add-dev-{uuid.uuid4()}",
            internal_user_id=internal_user_id,
            amount_kopecks=-cost_kopecks,
            transaction_type="subscription_payment",
            related_user_id=None,
            related_plan_id=snap.plan_id,
            description=f"add device: {current} → {new_count}",
            created_at=now,
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
    current = snap.device_count if snap and snap.device_count else DEVICES_DEFAULT

    if current <= DEVICES_DEFAULT:
        plan_name = plan_display_name(snap.plan_id) if snap and snap.plan_id else None
        active_until = snap.active_until_utc.date().isoformat() if snap and snap.active_until_utc else None
        return text_settings(
            True, plan_name=plan_name, device_count=current, active_until=active_until
        ), settings_keyboard(True, current)

    new_count = max(DEVICES_DEFAULT, current - 1)
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

    current = snap.device_count or DEVICES_DEFAULT
    if new_count >= current or new_count < DEVICES_DEFAULT:
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

    elif code == CB_LINK_EMAIL:
        # Invalidate all previous pending codes so user can start fresh
        pool = _get_pool_from_composition(composition)
        if pool is not None and uid is not None:
            from datetime import UTC, datetime

            await pool.execute(
                """UPDATE email_verification_codes SET used_at = $1
                   WHERE telegram_user_id = $2 AND purpose = 'link_email' AND used_at IS NULL""",
                datetime.now(UTC),
                uid,
            )
        # Check if email already linked
        existing_email = await _get_linked_email(composition, uid)
        text = text_link_email_intro(existing_email)
        keyboard = link_email_keyboard()

    elif code == CB_RESEND_EMAIL_CODE:
        text, keyboard = await _handle_resend_email_code(composition, uid)

    elif code == "identity_ready":
        # Show trial offer for new users who haven't used trial yet
        trial_available = await _is_trial_available(composition, uid)
        text = text_welcome(trial_available=trial_available)
        keyboard = welcome_keyboard(trial_available=trial_available)

    elif code == CB_TRIAL:
        text, keyboard = await _handle_trial_activation(composition, uid)

    elif code in (CB_MY_SUB, "store_success", "store_success_active"):
        text, keyboard = await _render_subscription_status(composition, uid, cid)

    elif code == CB_CONNECT_DEVICE:
        text, keyboard = text_connect_device(), connect_device_keyboard()

    elif code in (CB_CONNECT_WIN, CB_CONNECT_ANDROID, CB_CONNECT_IOS, CB_CONNECT_MAC):
        text = text_connect_platform(code)
        keyboard = connect_platform_keyboard()

    elif code == CB_CONNECT_NEXT:
        if uid is not None and composition.vless_provider is not None:
            from app.issuance.vless_provider import VlessProviderOutcome

            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                vless_result = await composition.vless_provider.get_user_config(
                    internal_user_id=id_rec.internal_user_id,
                )
                if vless_result.outcome == VlessProviderOutcome.SUCCESS and vless_result.config is not None:
                    text, keyboard = text_connect_config(vless_result.config), connect_config_keyboard()
                else:
                    text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)
            else:
                text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)
        else:
            text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)

    elif code == CB_CONNECT_DONE:
        text, keyboard = text_connect_done(), connect_done_keyboard()

    elif code == CB_MY_KEYS:
        has_sub = await _has_active_subscription(composition, uid)
        if has_sub and uid is not None and composition.vless_provider is not None:
            from app.issuance.vless_provider import VlessProviderOutcome

            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                vless_result = await composition.vless_provider.get_user_config(
                    internal_user_id=id_rec.internal_user_id,
                )
                if vless_result.outcome == VlessProviderOutcome.SUCCESS and vless_result.config is not None:
                    text = text_my_keys(vless_result.config)
                    keyboard = keys_keyboard()
                else:
                    text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)
            else:
                text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)
        else:
            text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)

    elif code == CB_REISSUE_KEYS:
        text, keyboard = text_reissue_confirm(), reissue_confirm_keyboard()

    elif code == CB_REISSUE_CONFIRM:
        if uid is not None and composition.vless_provider is not None:
            from app.issuance.vless_provider import VlessProviderOutcome

            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                await composition.vless_provider.revoke_user(internal_user_id=id_rec.internal_user_id)
                vless_result = await composition.vless_provider.create_user(internal_user_id=id_rec.internal_user_id)
                if vless_result.outcome == VlessProviderOutcome.SUCCESS and vless_result.config is not None:
                    text = "✅ Ключи перевыпущены!\n\n" + text_my_keys(vless_result.config)
                    keyboard = keys_keyboard()
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
            text = text_device_select(plan_id, plan.price_rubles, plan.duration_months, DEVICES_DEFAULT)
            keyboard = device_select_keyboard(plan_id, DEVICES_DEFAULT)

    elif code.startswith(CB_DEVICES):
        parts = code[len(CB_DEVICES) :].split(":")
        plan_id = parts[0] if parts else ""
        device_count = int(parts[1]) if len(parts) > 1 else DEVICES_DEFAULT
        plan = get_plan(plan_id)
        if plan is not None:
            text = text_device_select(plan_id, plan.price_rubles, plan.duration_months, device_count)
            keyboard = device_select_keyboard(plan_id, device_count)

    elif code.startswith(CB_CONFIRM_PAY):
        parts = code[len(CB_CONFIRM_PAY) :].split(":")
        plan_id = parts[0] if parts else ""
        device_count = int(parts[1]) if len(parts) > 1 else DEVICES_DEFAULT
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
        device_count = int(parts[1]) if len(parts) > 1 else DEVICES_DEFAULT
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
    # Check for email linking text input (email address or verification code)
    uid = _extract_private_telegram_user_id(update)
    if uid is not None and not isinstance(update.get("callback_query"), Mapping):
        msg = update.get("message")
        if isinstance(msg, Mapping):
            user_text = msg.get("text", "")
            if isinstance(user_text, str) and user_text.strip() and not user_text.startswith("/"):
                email_result = await _handle_email_linking(composition, uid, user_text.strip())
                if email_result is not None:
                    email_text, email_keyboard = email_result
                    return RenderedMessagePackage(
                        message_text=email_text,
                        action_keys=(),
                        correlation_id=correlation_id or uuid.uuid4().hex,
                        reply_markup=email_keyboard,
                        replay_suppresses_outbound=False,
                        uc01_idempotency_key=None,
                    )

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
