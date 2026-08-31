"""Тонкий facade runtime slice 1: сырой Telegram-подобный update → пакет отрендеренного сообщения (без SDK, без сервера).

Оркестрирует adapter → service/dispatch → storefront UI rendering / outbound keys → рендер каталога сообщений.
Сырые обновления не пересекают границу адаптера; этот модуль не принимает типы Telegram SDK.
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.application.bootstrap import Slice1Composition
from app.application.plan_pricing import get_plan_pricing
from app.application.purchase_handler import (
    PurchaseSummary,
    plan_options_from_plans,
)
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
    CB_ALL_KEYS,
    CB_ANDROID_DID_WORK,
    CB_ANDROID_HAPP_STEP,
    CB_ANDROID_NO,
    CB_ANDROID_RETRY,
    CB_ANDROID_STEP,
    CB_ANDROID_YES,
    CB_BALANCE,
    CB_BROADCAST_DAYS,
    CB_BROADCAST_PROMO,
    CB_BUY_VPN,
    CB_CONFIRM_PAY,
    CB_CONNECT_ANDROID,
    CB_CONNECT_DEVICE,
    CB_CONNECT_DONE,
    CB_CONNECT_IOS,
    CB_CONNECT_MAC,
    CB_CONNECT_NEXT,
    CB_CONNECT_TV,
    CB_CONNECT_WIN,
    CB_CUSTOM_DAYS,
    CB_DEVICES,
    CB_DO_PAY,
    CB_DO_PAY_CRYPTO,
    CB_HELP,
    CB_IOS_DID_WORK,
    CB_IOS_HAPP_STEP,
    CB_IOS_NO,
    CB_IOS_RETRY,
    CB_IOS_STEP,
    CB_IOS_YES,
    CB_LINK_EMAIL,
    CB_MAC_DID_WORK,
    CB_MAC_HAPP_STEP,
    CB_MAC_NO,
    CB_MAC_RETRY,
    CB_MAC_STEP,
    CB_MAC_YES,
    CB_MAIN_MENU,
    CB_MY_KEYS,
    CB_MY_SUB,
    CB_PAY_BALANCE,
    CB_PLAN,
    CB_PROMO_AT,
    CB_PROMOCODE,
    CB_REFERRAL,
    CB_REISSUE_CONFIRM,
    CB_REISSUE_KEYS,
    CB_REMOVE_DEVICE,
    CB_RESEND_EMAIL_CODE,
    CB_ROUTER,
    CB_SERVER,
    CB_SETTINGS,
    CB_TRIAL,
    CB_TV_DID_WORK,
    CB_TV_NO,
    CB_TV_RETRY,
    CB_TV_STEP,
    CB_TV_YES,
    CB_WIN_DID_WORK,
    CB_WIN_NO,
    CB_WIN_RETRY,
    CB_WIN_STEP,
    CB_WIN_YES,
    add_device_confirm_keyboard,
    add_device_select_keyboard,
    all_keys_list_keyboard,
    android_app_chooser_keyboard,
    android_did_work_keyboard,
    android_happ_step_1_keyboard,
    android_happ_step_1_text,
    android_happ_step_2_keyboard,
    android_happ_step_2_text,
    android_happ_step_3_keyboard,
    android_happ_step_3_text,
    android_happ_step_4_keyboard,
    android_happ_step_4_text,
    android_happ_step_5_keyboard,
    android_happ_step_5_text,
    android_happ_step_6_keyboard,
    android_happ_step_6_text,
    android_problem_keyboard,
    android_step_1_keyboard,
    android_step_1_text,
    android_step_2_keyboard,
    android_step_2_text,
    android_step_3_keyboard,
    android_step_3_text,
    android_step_4_keyboard,
    android_step_4_text,
    android_step_5_keyboard,
    android_step_5_text,
    android_step_6_keyboard,
    android_step_6_text,
    back_only_keyboard,
    balance_keyboard,
    buy_vpn_keyboard,
    confirm_pay_keyboard,
    connect_device_keyboard,
    custom_days_prompt_keyboard,
    device_select_keyboard,
    ios_app_chooser_keyboard,
    ios_did_work_keyboard,
    ios_did_work_text,
    ios_happ_step_1_keyboard,
    ios_happ_step_1_text,
    ios_happ_step_2_keyboard,
    ios_happ_step_2_text,
    ios_happ_step_3_keyboard,
    ios_happ_step_3_text,
    ios_happ_step_4_keyboard,
    ios_happ_step_4_text,
    ios_happ_step_5_keyboard,
    ios_happ_step_5_text,
    ios_happ_step_6_keyboard,
    ios_happ_step_6_text,
    ios_problem_keyboard,
    ios_problem_text,
    ios_step_1_keyboard,
    ios_step_1_text,
    ios_step_2_keyboard,
    ios_step_2_text,
    ios_step_3_keyboard,
    ios_step_3_text,
    ios_step_4_keyboard,
    ios_step_4_text,
    ios_step_5_keyboard,
    ios_step_5_text,
    ios_step_6_keyboard,
    ios_step_6_text,
    ios_success_text,
    keys_keyboard,
    link_email_code_keyboard,
    link_email_keyboard,
    mac_app_chooser_keyboard,
    mac_did_work_keyboard,
    mac_happ_step_1_keyboard,
    mac_happ_step_1_text,
    mac_happ_step_2_keyboard,
    mac_happ_step_2_text,
    mac_happ_step_3_keyboard,
    mac_happ_step_3_text,
    mac_happ_step_4_keyboard,
    mac_happ_step_4_text,
    mac_happ_step_5_keyboard,
    mac_happ_step_5_text,
    mac_happ_step_6_keyboard,
    mac_happ_step_6_text,
    mac_problem_keyboard,
    mac_step_1_keyboard,
    mac_step_1_text,
    mac_step_2_keyboard,
    mac_step_2_text,
    mac_step_3_keyboard,
    mac_step_3_text,
    mac_step_4_keyboard,
    mac_step_4_text,
    mac_step_5_keyboard,
    mac_step_5_text,
    mac_step_6_keyboard,
    mac_step_6_text,
    main_menu_keyboard,
    my_keys_menu_keyboard,
    my_subscription_keyboard,
    no_subscription_keyboard,
    promo_badge_label,
    promo_menu_badge,
    promo_offer_preview,
    promo_prompt_keyboard,
    reissue_confirm_keyboard,
    remove_device_keyboard,
    settings_keyboard,
    single_server_key_keyboard,
    text_add_device_confirm,
    text_add_device_intro,
    text_add_device_success,
    text_add_device_unavailable,
    text_all_keys_list,
    text_android_app_chooser,
    text_balance,
    text_balance_insufficient,
    text_balance_payment_in_progress,
    text_balance_payment_success,
    text_buy_vpn_intro,
    text_connect_device,
    text_custom_days_invalid,
    text_custom_days_prompt,
    text_device_select,
    text_error_generic,
    text_help,
    text_ios_app_chooser,
    text_keys_not_available,
    text_link_email_already_linked,
    text_link_email_code_sent,
    text_link_email_error,
    text_link_email_intro,
    text_link_email_success,
    text_mac_app_chooser,
    text_main_menu,
    text_my_keys,
    text_my_keys_menu,
    text_no_subscription,
    text_payment_unavailable,
    text_promo_already_used,
    text_promo_applied,
    text_promo_applied_in_order,
    text_promo_code_invalid,
    text_promo_ineligible,
    text_promo_not_found,
    text_promo_prompt,
    text_promo_rate_limited,
    text_purchase_summary,
    text_referral_program,
    text_reissue_confirm,
    text_remove_device_confirm,
    text_remove_device_success,
    text_router_soon,
    text_settings,
    text_single_server_key,
    text_subscription_active,
    text_subscription_expired,
    text_welcome,
    tv_did_work_keyboard,
    tv_problem_keyboard,
    tv_step_1_keyboard,
    tv_step_1_text,
    tv_step_2_keyboard,
    tv_step_2_text,
    tv_step_3_keyboard,
    tv_step_3_text,
    tv_step_4_keyboard,
    tv_step_4_text,
    tv_step_5_keyboard,
    tv_step_5_text,
    welcome_keyboard,
    win_did_work_keyboard,
    win_problem_keyboard,
    win_step_1_keyboard,
    win_step_1_text,
    win_step_2_keyboard,
    win_step_2_text,
    win_step_3_keyboard,
    win_step_3_text,
    win_step_4_keyboard,
    win_step_4_text,
    win_step_5_keyboard,
    win_step_5_text,
    win_step_6_keyboard,
    win_step_6_text,
)
from app.domain.devices import DEFAULT_DEVICE_LIMIT as DEVICES_DEFAULT
from app.domain.plans import get_plan, make_custom_plan_id, plan_display_name
from app.security.validation import ValidationError, validate_telegram_user_id
from app.shared.types import SafeUserStatusCategory

if TYPE_CHECKING:
    # Только для аннотаций (from __future__ import annotations): datetime импортируется
    # локально внутри функций, но встречается в сигнатуре _apply_promo_to_user.
    from datetime import datetime

_LOGGER = logging.getLogger(__name__)

# ─── Custom days input state ────────────────────────────────────────

_awaiting_custom_days: dict[int, bool] = {}
# Флаг FSM «ждём ввод промокода текстом» — параллелен _awaiting_custom_days.
# Значение = callback возврата (CB_MAIN_MENU из меню либо f"{CB_CONFIRM_PAY}...:N" из
# сводки покупки) — куда вернуться после успешного ввода. Сам применённый промокод
# хранится в БД (promo_applied), а не здесь: этот флаг лишь переключает text-input
# guard, чтобы сообщение-код не ушло в email-linking.
_awaiting_promo_code: dict[int, str] = {}
_last_connect_platform: dict[int, str] = {}

# Debounce accidental double-click on the "pay with balance" inline button: two rapid
# callbacks would otherwise debit twice and credit the referrer's commission twice.
# The window must cover the WHOLE processing — including VLESS retries (3×2s = up to 6s),
# otherwise a second click arriving after the old 3s window but while the first payment
# is still in VLESS-retry would double-debit + double-commission. 30s covers VLESS
# worst-case with margin; a legit repeat renewal 30s+ later is unaffected. Bot runs a
# single polling worker, so an in-process window is sufficient here. (Server-side
# idempotency like the web path's description-dedup + advisory lock is a Phase 3 refactor
# that shares one balance-payment service across bot and web.)
_balance_payment_last_click: dict[int, float] = {}
_BALANCE_PAYMENT_DEBOUNCE_SECONDS = 30.0

_INMEMORY_DICT_MAX = 10_000


def _trim_inmemory_dict(d: dict[int, Any], *, max_size: int = _INMEMORY_DICT_MAX) -> None:
    """Evict oldest entries when an in-memory dict exceeds max_size."""
    if len(d) > max_size:
        # dict preserves insertion order in Python 3.7+; drop the first 20%.
        evict = max_size // 5
        for _ in range(evict):
            d.pop(next(iter(d)), None)


# ─── Media path constants per platform/step ──────────────────────────

_STEP_MEDIA: dict[tuple[str, int], tuple[str | None, str | None]] = {
    # iOS
    ("ios", 2): ("video", "media/ios_step2.mp4"),
    ("ios", 3): ("video", "media/ios_step3.mp4"),
    ("ios", 4): ("photo", "media/ios_step4.jpg"),
    # Mac (reuses same media as iOS for steps 2-4)
    ("mac", 2): ("video", "media/ios_step2.mp4"),
    ("mac", 3): ("video", "media/ios_step3.mp4"),
    ("mac", 4): ("photo", "media/ios_step4.jpg"),
    # Android
    ("android", 3): ("video", "videos/Android.mp4"),
    # TV
    ("tv", 2): ("photo", "media/tv_step2.jpeg"),
    ("tv", 3): ("photo", "media/tv_step3.jpeg"),
    ("tv", 4): ("photo", "media/tv_step4.jpeg"),
    ("tv", 5): ("photo", "media/tv_step5.jpeg"),
    # Windows
    ("win", 1): ("document", "media/karing_windows_x64.exe"),
    ("win", 2): ("video", "media/ios_step2.mp4"),
    ("win", 3): ("video", "media/ios_step3.mp4"),
    ("win", 4): ("photo", "media/ios_step4.jpg"),
}


def _did_work_kb_for(code: str) -> dict[str, Any]:
    if code in (CB_MAC_DID_WORK,):
        return mac_did_work_keyboard()
    if code in (CB_TV_DID_WORK,):
        return tv_did_work_keyboard()
    if code in (CB_WIN_DID_WORK,):
        return win_did_work_keyboard()
    if code in (CB_ANDROID_DID_WORK,):
        return android_did_work_keyboard()
    return ios_did_work_keyboard()


def _problem_kb_for(code: str) -> dict[str, Any]:
    if code in (CB_MAC_NO, CB_MAC_RETRY):
        return mac_problem_keyboard()
    if code in (CB_TV_NO, CB_TV_RETRY):
        return tv_problem_keyboard()
    if code in (CB_WIN_NO, CB_WIN_RETRY):
        return win_problem_keyboard()
    if code in (CB_ANDROID_NO, CB_ANDROID_RETRY):
        return android_problem_keyboard()
    return ios_problem_keyboard()


def _step_1_for_retry(code: str) -> tuple[str, dict[str, Any] | None]:
    if code == CB_MAC_RETRY:
        return mac_step_1_text(), mac_step_1_keyboard()
    if code == CB_TV_RETRY:
        return tv_step_1_text(), tv_step_1_keyboard()
    if code == CB_WIN_RETRY:
        return win_step_1_text(), win_step_1_keyboard()
    if code == CB_ANDROID_RETRY:
        return android_step_1_text(), android_step_1_keyboard()
    return ios_step_1_text(), ios_step_1_keyboard()


async def _render_platform_step(
    code: str,
    uid: int | None,
    composition: Slice1Composition,
) -> tuple[str, dict[str, Any] | None, str | None, str | None]:
    """Render a platform step (ios/mac/tv/win) and return (text, keyboard, media_type, media_path)."""
    # Parse "ios_step:3" → ("ios", "karing", 3); "ios_happ_step:3" → ("ios", "happ", 3)
    platform: str | None = None
    app = "karing"
    step_str: str | None = None
    for prefix, _plat, _app in (
        (CB_IOS_HAPP_STEP, "ios", "happ"),
        (CB_MAC_HAPP_STEP, "mac", "happ"),
        (CB_ANDROID_HAPP_STEP, "android", "happ"),
        (CB_IOS_STEP, "ios", "karing"),
        (CB_MAC_STEP, "mac", "karing"),
        (CB_TV_STEP, "tv", "karing"),
        (CB_WIN_STEP, "win", "karing"),
        (CB_ANDROID_STEP, "android", "karing"),
    ):
        if code.startswith(prefix):
            platform = _plat
            app = _app
            step_str = code[len(prefix) :]
            break
    if platform is None or step_str is None:
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU), None, None

    try:
        step = int(step_str)
    except (ValueError, TypeError):
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU), None, None

    # Keyless users (no active subscription / no trial) must not walk into connect steps:
    # step 3 builds a Karing deep-link from the subscription URL, which is empty without a
    # subscription -> an invalid "?open=karing" URL button -> Telegram rejects the message.
    # Show a "no subscription" prompt with Buy + Main menu buttons instead.
    if not await _has_active_subscription(composition, uid):
        return text_no_subscription(), no_subscription_keyboard(), None, None

    # Fetch subscription URL for step 3
    subscription_url: str | None = None
    if step == 3 and uid is not None and composition.vless_provider is not None:
        from app.issuance.vless_provider import VlessProviderOutcome

        id_rec = await composition.identity.find_by_telegram_user_id(uid)
        if id_rec is not None:
            vless_result = await composition.vless_provider.get_user_config(
                internal_user_id=id_rec.internal_user_id,
            )
            if vless_result.outcome == VlessProviderOutcome.SUCCESS and vless_result.config is not None:
                subscription_url = vless_result.config.subscription_url

    # Happ branch: whitelist-bypass app for ios/mac/android. No media (the Karing
    # step videos show the Karing UI and must not be reused for Happ) → text-only.
    # Import is manual (copy subscription URL → paste in Happ), so step 3 shows the
    # URL in <code> rather than building an app deep-link.
    if app == "happ":
        url = subscription_url or ""
        if platform == "ios":
            if step == 1:
                text, keyboard = ios_happ_step_1_text(), ios_happ_step_1_keyboard()
            elif step == 2:
                text, keyboard = ios_happ_step_2_text(), ios_happ_step_2_keyboard()
            elif step == 3:
                text, keyboard = ios_happ_step_3_text(url), ios_happ_step_3_keyboard()
            elif step == 4:
                text, keyboard = ios_happ_step_4_text(), ios_happ_step_4_keyboard()
            elif step == 5:
                text, keyboard = ios_happ_step_5_text(), ios_happ_step_5_keyboard()
            elif step == 6:
                text, keyboard = ios_happ_step_6_text(), ios_happ_step_6_keyboard()
            else:
                text, keyboard = text_error_generic(), back_only_keyboard(CB_MAIN_MENU)
        elif platform == "mac":
            if step == 1:
                text, keyboard = mac_happ_step_1_text(), mac_happ_step_1_keyboard()
            elif step == 2:
                text, keyboard = mac_happ_step_2_text(), mac_happ_step_2_keyboard()
            elif step == 3:
                text, keyboard = mac_happ_step_3_text(url), mac_happ_step_3_keyboard()
            elif step == 4:
                text, keyboard = mac_happ_step_4_text(), mac_happ_step_4_keyboard()
            elif step == 5:
                text, keyboard = mac_happ_step_5_text(), mac_happ_step_5_keyboard()
            elif step == 6:
                text, keyboard = mac_happ_step_6_text(), mac_happ_step_6_keyboard()
            else:
                text, keyboard = text_error_generic(), back_only_keyboard(CB_MAIN_MENU)
        elif platform == "android":
            if step == 1:
                text, keyboard = android_happ_step_1_text(), android_happ_step_1_keyboard()
            elif step == 2:
                text, keyboard = android_happ_step_2_text(), android_happ_step_2_keyboard()
            elif step == 3:
                text, keyboard = android_happ_step_3_text(url), android_happ_step_3_keyboard()
            elif step == 4:
                text, keyboard = android_happ_step_4_text(), android_happ_step_4_keyboard()
            elif step == 5:
                text, keyboard = android_happ_step_5_text(), android_happ_step_5_keyboard()
            elif step == 6:
                text, keyboard = android_happ_step_6_text(), android_happ_step_6_keyboard()
            else:
                text, keyboard = text_error_generic(), back_only_keyboard(CB_MAIN_MENU)
        else:
            text, keyboard = text_error_generic(), back_only_keyboard(CB_MAIN_MENU)
        return text, keyboard, None, None

    # Dispatch to step text/keyboard functions
    if platform == "ios":
        if step == 1:
            text, keyboard = ios_step_1_text(), ios_step_1_keyboard()
        elif step == 2:
            text, keyboard = ios_step_2_text(), ios_step_2_keyboard()
        elif step == 3:
            url = subscription_url or ""
            text, keyboard = ios_step_3_text(url), ios_step_3_keyboard(url)
        elif step == 4:
            text, keyboard = ios_step_4_text(), ios_step_4_keyboard()
        elif step == 5:
            text, keyboard = ios_step_5_text(), ios_step_5_keyboard()
        elif step == 6:
            text, keyboard = ios_step_6_text(), ios_step_6_keyboard()
        else:
            text, keyboard = text_error_generic(), back_only_keyboard(CB_MAIN_MENU)
    elif platform == "mac":
        if step == 1:
            text, keyboard = mac_step_1_text(), mac_step_1_keyboard()
        elif step == 2:
            text, keyboard = mac_step_2_text(), mac_step_2_keyboard()
        elif step == 3:
            url = subscription_url or ""
            text, keyboard = mac_step_3_text(url), mac_step_3_keyboard(url)
        elif step == 4:
            text, keyboard = mac_step_4_text(), mac_step_4_keyboard()
        elif step == 5:
            text, keyboard = mac_step_5_text(), mac_step_5_keyboard()
        elif step == 6:
            text, keyboard = mac_step_6_text(), mac_step_6_keyboard()
        else:
            text, keyboard = text_error_generic(), back_only_keyboard(CB_MAIN_MENU)
    elif platform == "tv":
        if step == 1:
            text, keyboard = tv_step_1_text(), tv_step_1_keyboard()
        elif step == 2:
            text, keyboard = tv_step_2_text(), tv_step_2_keyboard()
        elif step == 3:
            url = subscription_url or ""
            text, keyboard = tv_step_3_text(url), tv_step_3_keyboard()
        elif step == 4:
            text, keyboard = tv_step_4_text(), tv_step_4_keyboard()
        elif step == 5:
            text, keyboard = tv_step_5_text(), tv_step_5_keyboard()
        else:
            text, keyboard = text_error_generic(), back_only_keyboard(CB_MAIN_MENU)
    elif platform == "win":
        if step == 1:
            text, keyboard = win_step_1_text(), win_step_1_keyboard()
        elif step == 2:
            text, keyboard = win_step_2_text(), win_step_2_keyboard()
        elif step == 3:
            url = subscription_url or ""
            text, keyboard = win_step_3_text(url), win_step_3_keyboard(url)
        elif step == 4:
            text, keyboard = win_step_4_text(), win_step_4_keyboard()
        elif step == 5:
            text, keyboard = win_step_5_text(), win_step_5_keyboard()
        elif step == 6:
            text, keyboard = win_step_6_text(), win_step_6_keyboard()
        else:
            text, keyboard = text_error_generic(), back_only_keyboard(CB_MAIN_MENU)
    elif platform == "android":
        if step == 1:
            text, keyboard = android_step_1_text(), android_step_1_keyboard()
        elif step == 2:
            text, keyboard = android_step_2_text(), android_step_2_keyboard()
        elif step == 3:
            url = subscription_url or ""
            text, keyboard = android_step_3_text(url), android_step_3_keyboard(url)
        elif step == 4:
            text, keyboard = android_step_4_text(), android_step_4_keyboard()
        elif step == 5:
            text, keyboard = android_step_5_text(), android_step_5_keyboard()
        elif step == 6:
            text, keyboard = android_step_6_text(), android_step_6_keyboard()
        else:
            text, keyboard = text_error_generic(), back_only_keyboard(CB_MAIN_MENU)
    else:
        text, keyboard = text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    media_type, media_path = _STEP_MEDIA.get((platform, step), (None, None))
    return text, keyboard, media_type, media_path


def _handle_custom_days_input(
    uid: int,
    user_text: str,
) -> tuple[str, dict[str, Any] | None, bool]:
    """Process user's custom days text input. Returns (text, keyboard, is_valid)."""
    try:
        days = int(user_text)
    except ValueError:
        return text_custom_days_invalid(user_text), custom_days_prompt_keyboard(), False
    if not (1 <= days <= 365):
        return text_custom_days_invalid(user_text), custom_days_prompt_keyboard(), False
    plan_id = make_custom_plan_id(days)
    plan = get_plan(plan_id)
    if plan is None:
        return text_custom_days_invalid(user_text), custom_days_prompt_keyboard(), False
    text = text_device_select(plan_id, plan.price_rubles, plan.duration_days, DEVICES_DEFAULT)
    keyboard = device_select_keyboard(plan_id, DEVICES_DEFAULT)
    return text, keyboard, True


async def _active_applied_promo(
    composition: Slice1Composition,
    uid: int | None,
) -> tuple[Any, Any] | None:
    """Закреплённый промокод для отображения — (promo, expires_at) либо None.

    Каноническая re-валидация (как при оплате): запись есть и не протухла, код
    существует, активен/в сроке/лимит не исчерпан, per-user лимит не исчерпан,
    аудитория подходит. expires_at нужен бейджу «ещё N ч».
    """
    if uid is None:
        return None
    from datetime import UTC, datetime

    from app.application.promo_service import compute_is_new_user
    from app.domain.promo import audience_eligible, per_user_exhausted, redeem_block_reason

    resolved_iid = (await _get_internal_user_id(composition, uid)) or f"u{uid}"
    now = datetime.now(UTC)
    applied = await composition.promo_applied_repo.get(resolved_iid)
    if applied is None or applied.is_expired(now):
        return None
    promo = await composition.promo_code_repo.get(applied.code)
    if promo is None or redeem_block_reason(promo, now) is not None:
        return None
    used = await composition.promo_redemption_repo.count_for_user(promo.code, resolved_iid)
    if per_user_exhausted(promo, used):
        # Погашавший once-код с «висящим» пином (сбой clear) — без бейджа/скидки,
        # консистентно с оплатой (resolve_active_promo тоже вернёт None).
        return None
    is_new = await compute_is_new_user(
        pool=_get_pool_from_composition(composition),
        snapshots=composition.snapshots,
        internal_user_id=resolved_iid,
    )
    if not audience_eligible(promo.audience, is_new_user=is_new):
        return None
    return promo, applied.expires_at


def _promo_remaining_hours(expires_at: Any) -> int:
    from datetime import UTC, datetime

    delta = expires_at - datetime.now(UTC)
    return max(0, int(delta.total_seconds() // 3600))


async def _promo_menu_badge(composition: Slice1Composition, uid: int | None) -> str:
    """Бейдж закреплённого промокода для меню/статуса ('' если промокода нет)."""
    active = await _active_applied_promo(composition, uid)
    if active is None:
        return ""
    promo, expires_at = active
    return promo_menu_badge(promo.code, promo_badge_label(promo), _promo_remaining_hours(expires_at))


async def _main_menu_text(composition: Slice1Composition, uid: int | None) -> str:
    return text_main_menu() + await _promo_menu_badge(composition, uid)


async def _render_purchase_summary(
    composition: Slice1Composition,
    uid: int | None,
    plan_id: str,
    device_count: int,
    *,
    promo_note: str = "",
) -> tuple[str, dict[str, Any] | None]:
    """Сводка заказа со скидкой закреплённого промокода + кнопкой ввода промокода.

    Скидка — best-of(промокод, глобальная): то же effective_plan_price, что применяется
    при оплате (CB_DO_PAY/CB_PAY_BALANCE пере-резолвят resolve_active_promo). План и
    глобальная скидка — через PlanPricingProvider (эффективные цены админки). Кнопка
    промокода ведёт в FSM с возвратом к этой сводке. Гейт «С баланса» ориентируется
    на скидочную сумму.
    """
    from app.application.purchase_handler import build_purchase_summary_for_plan
    from app.domain.pricing import effective_plan_price

    pricing = get_plan_pricing(_get_pool_from_composition(composition))
    plan = await pricing.get_plan(plan_id)
    if plan is None:
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)
    summary = build_purchase_summary_for_plan(plan, device_count)
    if not isinstance(summary, PurchaseSummary):
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)

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

    active = await _active_applied_promo(composition, uid)
    promo = active[0] if active is not None else None
    global_discount = await pricing.get_global_discount()
    text = text_purchase_summary(summary, promo=promo, promo_note=promo_note, global_discount=global_discount)

    eff = effective_plan_price(
        summary.total_price_rubles, plan_id=plan_id, promo=promo, global_discount=global_discount
    )
    keyboard = confirm_pay_keyboard(
        plan_id,
        device_count,
        balance_kopecks=user_balance_kopecks,
        total_kopecks=eff.discounted_kopecks,
        promo=promo,
    )
    return text, keyboard


async def _apply_promo_to_user(
    composition: Slice1Composition,
    internal_user_id: str,
    raw_code: str,
    *,
    now: datetime | None = None,
) -> tuple[str, str | None, Any | None]:
    """Apply-ядро промокода: normalize → get → redeem_block_reason → per-user → audience → upsert.

    Переиспользуется текстовым вводом (_handle_promo_code_input) и кнопкой рассылки
    (_handle_broadcast_promo_claim). НЕ трогает rate-limit / FSM / return_target / render —
    это остаётся в вызывающем коде. Принимает уже-resolved internal_user_id.

    Возвращает (status, code, promo):
      status ∈ {"ok", "invalid", "not_found", "inactive", "already_used", "ineligible"};
      promo — PromoCode при not_found-кроме (None только для invalid/not_found).
    already_used — per-user лимит исчерпан (код с max_redemptions_per_user=1 уже
    погашен этим пользователем). Скидка канонически пере-валидируется при оплате
    (resolve_active_promo), поэтому здесь сохраняется лишь «намерение» (promo_applied, TTL 24ч).
    """
    from datetime import UTC, datetime, timedelta

    from app.application.promo_service import compute_is_new_user
    from app.domain.promo import (
        PROMO_APPLIED_TTL_HOURS,
        audience_eligible,
        normalize_code,
        per_user_exhausted,
        redeem_block_reason,
        validate_code_format,
    )

    code = normalize_code(raw_code)
    if validate_code_format(code) is not None:
        return "invalid", code, None

    promo = await composition.promo_code_repo.get(code)
    if promo is None:
        return "not_found", code, None

    now = now or datetime.now(UTC)
    if redeem_block_reason(promo, now) is not None:
        # inactive/expired/exhausted — вызывающий решает, как показать (обычно «не найден»).
        return "inactive", code, promo

    used = await composition.promo_redemption_repo.count_for_user(code, internal_user_id)
    if per_user_exhausted(promo, used):
        return "already_used", code, promo

    is_new = await compute_is_new_user(
        pool=_get_pool_from_composition(composition),
        snapshots=composition.snapshots,
        internal_user_id=internal_user_id,
    )
    if not audience_eligible(promo.audience, is_new_user=is_new):
        return "ineligible", code, promo

    expires_at = now + timedelta(hours=PROMO_APPLIED_TTL_HOURS)
    await composition.promo_applied_repo.upsert(internal_user_id, code, expires_at)
    return "ok", code, promo


async def _handle_promo_code_input(
    composition: Slice1Composition,
    uid: int | None,
    user_text: str,
    return_target: str = CB_MAIN_MENU,
) -> tuple[str, dict[str, Any] | None]:
    """Обработать ввод промокода текстом → валидация → запись в promo_applied → текст.

    Возвращает (text, keyboard). ``return_target`` — callback возврата после успеха
    (CB_MAIN_MENU из главного меню либо f"{CB_CONFIRM_PAY}plan:N" из сводки покупки).
    Rate-limit — ключ PROMOCODE (5/мин, anti-bruteforce). Применённый промокод пишется
    в БД (promo_applied, TTL 24ч) — переживает деплой. Скидка канонически
    пере-валидируется при оплате (resolve_active_promo), поэтому здесь сохраняется лишь
    «намерение». Флаг FSM сбрасывается только при успехе (retry по опечатке).
    """
    from app.application.telegram_command_rate_limit import TelegramCommandRateLimitKey

    prompt_keyboard = promo_prompt_keyboard(back_callback=return_target)
    if uid is None:
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    # Storefront-колбэки обходят командный лимитер — явная проверка только ввода текстом.
    allowed = await composition.command_rate_limiter.allow(
        telegram_user_id=uid,
        command_key=TelegramCommandRateLimitKey.PROMOCODE,
    )
    if not allowed:
        return text_promo_rate_limited(), prompt_keyboard

    internal_user_id = await _get_internal_user_id(composition, uid)
    resolved_iid = internal_user_id or f"u{uid}"
    status, code, promo = await _apply_promo_to_user(composition, resolved_iid, user_text)
    if status == "invalid":
        return text_promo_code_invalid(user_text), prompt_keyboard
    if status in ("not_found", "inactive"):
        return text_promo_not_found(), prompt_keyboard
    if status == "already_used":
        return text_promo_already_used(), prompt_keyboard
    if status == "ineligible":
        return text_promo_ineligible(), prompt_keyboard

    # Успех — сбрасываем флаг FSM (до этого повторный ввод был возможен).
    _awaiting_promo_code.pop(uid, None)
    # Куда вернуться: из главного меню — в меню с бейджем; из оплаты — к сводке со скидкой.
    if return_target.startswith(CB_CONFIRM_PAY):
        rest = return_target[len(CB_CONFIRM_PAY) :]
        sep = rest.rfind(":")
        plan_id = rest[:sep] if sep >= 0 else rest
        try:
            device_count = int(rest[sep + 1 :]) if sep >= 0 else DEVICES_DEFAULT
        except ValueError:
            device_count = DEVICES_DEFAULT
        note = text_promo_applied_in_order(code, promo_badge_label(promo))
        return await _render_purchase_summary(composition, uid, plan_id, device_count, promo_note=note)
    return (
        text_promo_applied(code, offer_text=promo_offer_preview(promo)),
        await _main_menu_keyboard(composition, uid),
    )


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

# All storefront callbacks now use HTML parse mode (more robust than Markdown).
# Previously some used Markdown v1 (fragile with special chars in URLs/keys).

_CALLBACK_ONLY_STOREFRONT = frozenset(
    {
        CB_MAIN_MENU,
        CB_BUY_VPN,
        CB_CUSTOM_DAYS,
        CB_PROMOCODE,
        CB_MY_SUB,
        CB_MY_KEYS,
        CB_ALL_KEYS,
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
        CB_REISSUE_KEYS,
        CB_REISSUE_CONFIRM,
        CB_RESEND_EMAIL_CODE,
        CB_IOS_DID_WORK,
        CB_IOS_YES,
        CB_IOS_NO,
        CB_IOS_RETRY,
        CB_MAC_DID_WORK,
        CB_MAC_YES,
        CB_MAC_NO,
        CB_MAC_RETRY,
        CB_CONNECT_TV,
        CB_TV_DID_WORK,
        CB_TV_YES,
        CB_TV_NO,
        CB_TV_RETRY,
        CB_WIN_DID_WORK,
        CB_WIN_YES,
        CB_WIN_NO,
        CB_WIN_RETRY,
        CB_ANDROID_DID_WORK,
        CB_ANDROID_YES,
        CB_ANDROID_NO,
        CB_ANDROID_RETRY,
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
                    CB_BROADCAST_DAYS,
                    CB_BROADCAST_PROMO,
                    CB_PLAN,
                    CB_DEVICES,
                    CB_CONFIRM_PAY,
                    CB_PAY_BALANCE,
                    CB_DO_PAY,
                    CB_DO_PAY_CRYPTO,
                    CB_ADD_DEV_BALANCE,
                    CB_ADD_DEV,
                    CB_PROMO_AT,
                    CB_SERVER,
                    CB_IOS_STEP,
                    CB_MAC_STEP,
                    CB_TV_STEP,
                    CB_WIN_STEP,
                    CB_ANDROID_STEP,
                    CB_IOS_HAPP_STEP,
                    CB_MAC_HAPP_STEP,
                    CB_ANDROID_HAPP_STEP,
                    "add_dev_pay:",
                    "add_dev_pay_crypto:",
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


async def _main_menu_keyboard(
    composition: Slice1Composition,
    uid: int | None,
) -> dict[str, Any]:
    """Main menu keyboard with the deferrable trial button surfaced at the top while the
    user's one-time trial is still available. The trial is NOT activated here — only when
    the user clicks the trial button (CB_TRIAL -> _handle_trial_activation)."""
    return main_menu_keyboard(
        trial_available=await _is_trial_available(composition, uid),
    )


async def _has_active_subscription(
    composition: Slice1Composition,
    uid: int | None,
) -> bool:
    """True if the user has an active subscription (trial or paid) — i.e. has VLESS keys.
    Gates the connect-device flow: a keyless user (no trial, no payment) gets a
    "no active subscription" prompt instead of walking into connect steps that need a
    subscription URL (which would render an invalid karing deep-link button)."""
    if uid is None:
        return False
    id_rec = await composition.identity.find_by_telegram_user_id(uid)
    if id_rec is None:
        return False
    snap = await composition.snapshots.get_for_user(id_rec.internal_user_id)
    return snap is not None and snap.state_label == "active"


async def _handle_trial_activation(
    composition: Slice1Composition,
    uid: int | None,
) -> tuple[str, dict[str, Any] | None]:
    """Activate 3-day trial: create VLESS keys, set trial dates.

    Uses atomic ``UPDATE ... WHERE trial_used = FALSE`` to prevent double-trial
    under concurrent requests.
    """
    from datetime import UTC, datetime

    from app.application.interfaces import SubscriptionSnapshot
    from app.domain.trial import trial_expires_at

    if uid is None:
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    id_rec = await composition.identity.find_by_telegram_user_id(uid)
    if id_rec is None:
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    pool = _get_pool_from_composition(composition)

    # Atomic claim: mark trial_used = TRUE only if it was FALSE.
    # If someone else already claimed it, rows_affected == 0 → reject.
    if pool is not None:
        result = await pool.execute(
            "UPDATE user_identities SET trial_used = TRUE WHERE internal_user_id = $1 AND (trial_used = FALSE OR trial_used IS NULL)",
            id_rec.internal_user_id,
        )
        if result == "UPDATE 0":
            return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)
    else:
        # In-memory fallback: non-atomic check (tests / dev only)
        snap = await composition.snapshots.get_for_user(id_rec.internal_user_id)
        if snap is not None and snap.trial_started_at is not None:
            return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    # Check VLESS provider available
    if composition.vless_provider is None:
        # Roll back trial claim
        if pool is not None:
            await pool.execute(
                "UPDATE user_identities SET trial_used = FALSE WHERE internal_user_id = $1",
                id_rec.internal_user_id,
            )
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    # Create VLESS user
    from app.issuance.vless_provider import VlessProviderOutcome

    vless_result = await composition.vless_provider.create_user(
        internal_user_id=id_rec.internal_user_id,
    )
    if vless_result.outcome != VlessProviderOutcome.SUCCESS or vless_result.config is None:
        # Roll back trial claim
        if pool is not None:
            await pool.execute(
                "UPDATE user_identities SET trial_used = FALSE WHERE internal_user_id = $1",
                id_rec.internal_user_id,
            )
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

    # Trial activated: show the main menu (NOT the full key list — it can exceed
    # Telegram's 4096-char limit and get rejected). The user reaches their keys via
    # "Мои ключи" / "Подключить устройство". trial_used is now TRUE, so the menu's
    # trial button is hidden. The "⏳ Идёт генерация ключей…" interim message +
    # delete-and-show-menu choreography is handled by the runtime (process_batch),
    # which detects CB_TRIAL and shows feedback during the slow key creation.
    return (
        "✅ <b>Пробный период активирован!</b>\n\n"
        "Ключи сгенерированы. Откройте «🔐 Мои ключи» или «📱 Подключить устройство».\n\n" + text_main_menu(),
        main_menu_keyboard(),
    )


def _broadcast_bonus_unavailable_text() -> str:
    """Единый текст отказа для бонус-кнопки. Не различает причины (уже получено / не получатель
    рассылки / истёк срок) — чтобы не раскрывать деталей. См. _handle_broadcast_days_claim."""
    return (
        "🎁 <b>Бонус недоступен</b>\n\n"
        "Эта кнопка уже использована, была адресована другому аккаунту "
        "или срок её действия истёк."
    )


async def _handle_broadcast_days_claim(
    composition: Slice1Composition,
    uid: int | None,
    job_id: str,
) -> tuple[str, dict[str, Any] | None]:
    """Обработчик кнопки «бесплатные дни» из рассылки.

    Atomic claim eligible→claimed в broadcast_bonus_claims (WHERE status='eligible' AND
    expires_at>NOW()) даёт одновременно: идемпотентность (двойной клик → UPDATE 0), «только
    получатели» (не-получатель отсутствует в таблице → UPDATE 0) и срок годности (старая
    рассылка → expires_at истёк → UPDATE 0). Затем — grant_subscription_days и закрытие
    триала.
    """
    if uid is None or not job_id:
        return _broadcast_bonus_unavailable_text(), back_only_keyboard(CB_MAIN_MENU)

    # Soft anti-spam (storefront-колбэки обходят диспетчерский лимитер — проверяем явно).
    from app.application.telegram_command_rate_limit import TelegramCommandRateLimitKey

    try:
        allowed = await composition.command_rate_limiter.allow(
            telegram_user_id=uid,
            command_key=TelegramCommandRateLimitKey.BROADCAST_CLAIM,
        )
    except Exception:
        allowed = True  # fail-open: сбой лимитера не должен блокировать выдачу бонуса
    if not allowed:
        return _broadcast_bonus_unavailable_text(), back_only_keyboard(CB_MAIN_MENU)

    id_rec = await composition.identity.find_by_telegram_user_id(uid)
    if id_rec is None:
        return _broadcast_bonus_unavailable_text(), back_only_keyboard(CB_MAIN_MENU)

    pool = _get_pool_from_composition(composition)
    if pool is None:
        return _broadcast_bonus_unavailable_text(), back_only_keyboard(CB_MAIN_MENU)

    # Atomic claim. UPDATE 0 покрывает все причины отказа — единый текст ниже.
    row = await pool.fetchrow(
        "UPDATE broadcast_bonus_claims SET status='claimed', claimed_at=NOW() "
        "WHERE job_id=$1 AND telegram_user_id=$2 AND status='eligible' AND expires_at > NOW() "
        "RETURNING days",
        job_id,
        uid,
    )
    if row is None:
        return _broadcast_bonus_unavailable_text(), back_only_keyboard(CB_MAIN_MENU)

    days = int(row["days"])
    from app.application.subscription_grant import grant_subscription_days

    result = await grant_subscription_days(
        pool=pool,
        vless_provider=composition.vless_provider,
        internal_user_id=id_rec.internal_user_id,
        days=days,
        actor=f"broadcast_claim:{job_id}",
        correlation_id=f"bc_claim:{job_id[:8]}:{uid}",
    )

    # Закрыть триал-квоту: иначе юзер сможет активировать 3-дневный триал, который
    # перезапишет active_until и сожжёт бонус. Минимум бонуса = 3 = триал, поэтому это
    # никогда не делает положение юзера хуже (бонус ≥ триала по длительности).
    try:
        await pool.execute(
            "UPDATE user_identities SET trial_used = TRUE "
            "WHERE internal_user_id = $1 AND (trial_used IS NULL OR trial_used = FALSE)",
            id_rec.internal_user_id,
        )
    except Exception:
        _LOGGER.exception(
            "broadcast claim: failed to close trial_used user=%s job=%s",
            id_rec.internal_user_id[:8],
            job_id[:8],
        )

    new_until = result.new_active_until.date().isoformat()
    if result.provisioning.startswith("success"):
        text = (
            f"🎁 <b>Бонус активирован: +{days} дн. бесплатно!</b>\n\n"
            f"Подписка продлена до <b>{new_until}</b>. Ключи готовы — "
            f"откройте «🔐 Мои ключи» или «📱 Подключить устройство».\n\n" + text_main_menu()
        )
    else:
        # VLESS-провайдер отсутствует/сбойнул — снапшот продлён, ключи добьёт reconcile-таймер.
        text = (
            f"🎁 <b>Бонус активирован: +{days} дн. бесплатно!</b>\n\n"
            f"Подписка продлена до <b>{new_until}</b>. Ключи будут готовы "
            f"в течение 30 минут — мы сгенерируем их автоматически.\n\n" + text_main_menu()
        )
    return text, main_menu_keyboard()


async def _handle_broadcast_promo_claim(
    composition: Slice1Composition,
    uid: int | None,
    job_id: str,
) -> tuple[str, dict[str, Any] | None]:
    """Обработчик кнопки промокода из рассылки.

    Реестр ``broadcast_promo_claims`` (заполнен строго реальными получателями при отправке)
    + gate ``expires_at>NOW()`` дают одновременно: «только получатели» (не-получатель
    отсутствует в таблице → gate None) и срок годности кнопки. Применение промокода
    идемпотентно (``promo_applied`` PK=internal_user_id), поэтому TOCTOU между gate и upsert
    доброкачественна — НЕ делаем atomic UPDATE…RETURNING как у дней.
    """
    if uid is None or not job_id:
        return _broadcast_bonus_unavailable_text(), back_only_keyboard(CB_MAIN_MENU)

    from datetime import UTC, datetime

    from app.application.telegram_command_rate_limit import TelegramCommandRateLimitKey

    try:
        allowed = await composition.command_rate_limiter.allow(
            telegram_user_id=uid,
            command_key=TelegramCommandRateLimitKey.BROADCAST_CLAIM,
        )
    except Exception:
        allowed = True  # fail-open: сбой лимитера не должен блокировать применение
    if not allowed:
        return _broadcast_bonus_unavailable_text(), back_only_keyboard(CB_MAIN_MENU)

    id_rec = await composition.identity.find_by_telegram_user_id(uid)
    if id_rec is None:
        return _broadcast_bonus_unavailable_text(), back_only_keyboard(CB_MAIN_MENU)

    pool = _get_pool_from_composition(composition)
    if pool is None:
        return _broadcast_bonus_unavailable_text(), back_only_keyboard(CB_MAIN_MENU)

    # Gate: только получатели + срок годности. None → единый отказ (не раскрываем причину).
    row = await pool.fetchrow(
        "SELECT code FROM broadcast_promo_claims WHERE job_id=$1 AND telegram_user_id=$2 AND expires_at > NOW()",
        job_id,
        uid,
    )
    if row is None:
        return _broadcast_bonus_unavailable_text(), back_only_keyboard(CB_MAIN_MENU)

    code = str(row["code"])
    now = datetime.now(UTC)

    # Pre-check «уже применён»: экономит audience-чек/запросы и даёт дружеложелательный
    # отклик на повторный клик (apply идемпотентен, но так дешевле).
    existing = await composition.promo_applied_repo.get(id_rec.internal_user_id)
    if existing is not None and not existing.is_expired(now) and existing.code == code:
        promo = await composition.promo_code_repo.get(code)
        offer = promo_offer_preview(promo) if promo is not None else ""
        return text_promo_applied(code, offer_text=offer), main_menu_keyboard()

    status, resolved_code, promo = await _apply_promo_to_user(composition, id_rec.internal_user_id, code, now=now)
    if status in ("not_found", "inactive", "invalid"):
        return text_promo_not_found(), back_only_keyboard(CB_MAIN_MENU)
    if status == "already_used":
        # once-код уже погашен этим пользователем (например, успел применить до рассылки).
        return text_promo_already_used(), back_only_keyboard(CB_MAIN_MENU)
    if status == "ineligible":
        return text_promo_ineligible(), back_only_keyboard(CB_MAIN_MENU)
    # ok
    return (
        text_promo_applied(resolved_code, offer_text=promo_offer_preview(promo)),
        main_menu_keyboard(),
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
        from app.domain.status_view import remaining_days as _remaining_days

        remaining_days = _remaining_days(result.active_until_utc) if result.active_until_utc is not None else None
        plan_name = None
        device_count = None
        id_rec = await composition.identity.find_by_telegram_user_id(uid)
        if id_rec is not None:
            snap = await composition.snapshots.get_for_user(id_rec.internal_user_id)
            if snap is not None:
                if snap.plan_id:
                    plan_name = plan_display_name(snap.plan_id)
                device_count = snap.device_count
        if device_count is None:
            device_count = DEVICES_DEFAULT
        # Ссылка подписки показывается прямо в подменю «Моя подписка» (тот же вызов
        # get_user_config, что и в обработчике CB_MY_KEYS). Без vless_provider / identity —
        # подменю без блока ссылки (функционал экранов от этого не страдает).
        subscription_url: str | None = None
        if composition.vless_provider is not None and uid is not None:
            from app.issuance.vless_provider import VlessProviderOutcome

            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                vless_result = await composition.vless_provider.get_user_config(
                    internal_user_id=id_rec.internal_user_id,
                )
                if vless_result.outcome == VlessProviderOutcome.SUCCESS and vless_result.config is not None:
                    subscription_url = vless_result.config.subscription_url
        text = text_subscription_active(
            active_until,
            plan_name,
            device_count,
            remaining_days=remaining_days,
        )
        if subscription_url:
            text += f"\n\n🔗 Ссылка для подписки:\n<code>{subscription_url}</code>"
        text += await _promo_menu_badge(composition, uid)
        return text, my_subscription_keyboard()
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
        from app.web_api.helpers import hash_code as _hash_code

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
        # Drop any emails previously linked to THIS telegram identity so the
        # partial unique index (one verified row per email) stays satisfied.
        await pool.execute(
            "DELETE FROM user_emails WHERE telegram_user_id = $1",
            uid,
        )

        # Merge a web-only account BEFORE inserting our own (uid, email) row.
        # Why order matters: user_emails has idx_user_emails_email_verified — at most
        # one VERIFIED row per email. A leftover verified web row (telegram_user_id
        # <= 0) would make the insert below raise a unique-violation. The merge
        # reassigns that web row onto `uid` (clearing the index) and migrates the
        # web account's data (subscription and referral) onto the real identity.
        from app.persistence.account_merge import merge_web_account_if_needed

        merged = await merge_web_account_if_needed(pool, uid, pending["email"])

        await pool.execute(
            """INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at)
               VALUES ($1, $2, TRUE, $3)
               ON CONFLICT (telegram_user_id, email) DO UPDATE SET is_verified = TRUE, verified_at = $3""",
            uid,
            pending["email"],
            now,
        )

        # A merged web account had its VLESS keys provisioned under web_<hash> — a
        # different per-transport uuid than u<uid>. Re-provision under the real
        # identity now (instead of waiting for the hourly reconcile) and revoke the
        # stale web clients so no orphan panel clients remain. Best-effort: never
        # blocks the link success.
        if merged:
            await _reprovision_keys_after_merge(composition, uid, pending["email"])

        return text_link_email_success(pending["email"]), await _main_menu_keyboard(composition, uid)

    # No pending verification — treat text as email
    if "@" not in text or "." not in text.split("@")[-1]:
        return None  # Not an email, let normal processing handle it

    email = text.lower().strip()

    # Check if already linked / claimed by another account
    existing = await pool.fetchrow(
        "SELECT telegram_user_id FROM user_emails WHERE email = $1 AND is_verified = TRUE",
        email,
    )
    if existing and existing["telegram_user_id"] == uid:
        return text_link_email_already_linked(email), await _main_menu_keyboard(composition, uid)
    if existing and existing["telegram_user_id"] > 0:
        # Verified email belongs to a DIFFERENT real Telegram account — refuse.
        # A web-only account has a non-positive telegram_user_id; claiming it by
        # proving inbox ownership is legitimate and triggers an account merge on
        # verify, so we let it through.
        return text_link_email_error("email_belongs_to_other_account"), link_email_keyboard()

    # Send verification code
    from datetime import UTC, datetime, timedelta

    from app.web_api.email_link import _MAX_SEND_PER_EMAIL_PER_HOUR
    from app.web_api.helpers import generate_code
    from app.web_api.helpers import hash_code as _hash

    recent = await pool.fetchval(
        """SELECT COUNT(*) FROM email_verification_codes
           WHERE email = $1 AND created_at > NOW() - INTERVAL '1 hour'""",
        email,
    )
    if recent is not None and recent >= _MAX_SEND_PER_EMAIL_PER_HOUR:
        return text_link_email_error("rate_limited"), link_email_keyboard()

    code = generate_code()
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


async def _reprovision_keys_after_merge(
    composition: Slice1Composition,
    uid: int,
    email: str,
) -> None:
    """After merging a web-only account into ``uid``, move its VLESS keys onto the
    real identity.

    A web account's keys were provisioned under ``web_<hash>`` — a different
    per-transport uuid than ``u<uid>`` — so after the merge they no longer resolve.
    Here we revoke the stale ``web_<hash>`` clients across all panels and, if the
    merged subscription is active, create fresh clients under ``u<uid>`` so the user
    has working keys immediately (instead of waiting for the hourly reconcile).

    Best-effort: a provider failure is logged and swallowed — the email link itself
    already succeeded and reconcile will heal the keys regardless.
    """
    import logging

    log = logging.getLogger(__name__)
    provider = getattr(composition, "vless_provider", None)
    pool = _get_pool_from_composition(composition)
    if provider is None or pool is None:
        return
    try:
        from app.persistence.account_merge import _web_internal_id

        old_web_id = _web_internal_id(email)
        # Revoke the stale web_<hash> clients (compute their uuids from old_web_id).
        # Suppress: a panel hiccup must not abort the link.
        with contextlib.suppress(Exception):
            await provider.revoke_user(internal_user_id=old_web_id)

        id_rec = await composition.identity.find_by_telegram_user_id(uid)
        if id_rec is None:
            return
        # Only re-create keys if the user has an active subscription.
        snap = await pool.fetchrow(
            "SELECT state_label, device_count FROM subscription_snapshots WHERE internal_user_id = $1",
            id_rec.internal_user_id,
        )
        if snap is None or snap["state_label"] != "active":
            return
        device_count = snap["device_count"]
        if device_count:
            await provider.create_user(internal_user_id=id_rec.internal_user_id, device_count=device_count)
        else:
            await provider.create_user(internal_user_id=id_rec.internal_user_id)
    except Exception:
        log.warning("email_link.reprovision_after_merge_failed uid=%s email=***", uid, exc_info=True)


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

    from app.web_api.helpers import generate_code
    from app.web_api.helpers import hash_code as _hash

    code = generate_code()
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


async def _process_yookassa_payment(
    composition: Slice1Composition,
    uid: int | None,
    *,
    plan_id: str,
    device_count: int,
) -> tuple[str, dict[str, Any] | None]:
    import logging
    import os

    from app.application.plan_pricing import charge_metadata_fields, resolve_effective_charge
    from app.domain.plans import calculate_total_price, plan_display_name
    from app.domain.pricing import WINNER_GLOBAL
    from app.yookassa.client import YooKassaClient

    _logger = logging.getLogger(__name__)

    if uid is None:
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)

    pricing = get_plan_pricing(_get_pool_from_composition(composition))
    plan = await pricing.get_plan(plan_id)
    if plan is None:
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)

    total_rubles = calculate_total_price(plan, device_count)

    # Применённый промокод: каноническая re-валидация (активность/срок/лимит/аудитория).
    # Итог — best-of(промокод, глобальная) через resolve_effective_charge: metadata несёт
    # base_amount_kopecks (фиксация базы против смены цен до вебхука) + скидку победителя;
    # promo_code кладётся ТОЛЬКО при победе промокода — глобальная скидка код не расходует.
    payment_metadata: dict[str, str] = {}
    internal_user_id = await _get_internal_user_id(composition, uid)
    promo = None
    promo_code = None
    if internal_user_id is not None:
        from app.application.promo_service import resolve_active_promo

        active = await resolve_active_promo(
            pool=_get_pool_from_composition(composition),
            promo_applied_repo=composition.promo_applied_repo,
            promo_code_repo=composition.promo_code_repo,
            promo_redemption_repo=composition.promo_redemption_repo,
            snapshots=composition.snapshots,
            internal_user_id=internal_user_id,
            base_rubles=total_rubles,
            plan_id=plan_id,
        )
        if active is not None:
            promo = active.promo
            promo_code = active.code
    global_discount = await pricing.get_global_discount()
    resolved = resolve_effective_charge(
        base_rubles=total_rubles,
        plan_id=plan_id,
        promo=promo,
        global_discount=global_discount,
    )
    charge_rubles = resolved.charge_rubles
    discount_kopecks = resolved.price.discount_kopecks
    payment_metadata.update(charge_metadata_fields(resolved, promo_code=promo_code, global_discount=global_discount))

    client = YooKassaClient.from_env()
    if client is None:
        _logger.warning("yookassa not configured, cannot create bot payment")
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)

    return_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://bravada-connect.ru").strip().rstrip("/")
    try:
        result = await client.create_payment(
            amount_rubles=charge_rubles,
            plan_id=plan_id,
            device_count=device_count,
            telegram_user_id=uid,
            return_url=f"{return_url}/dashboard",
            description=f"Bravada VPN — {plan_display_name(plan_id)}",
            metadata=payment_metadata,
        )
    except Exception:
        _logger.exception("yookassa create_payment failed in bot flow")
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)

    from app.bot_transport.payment_message_registry import set_pending_payment_for_user

    set_pending_payment_for_user(uid, result.payment_id)

    display_name = plan_display_name(plan_id)
    if discount_kopecks > 0 and resolved.price.winner == WINNER_GLOBAL:
        price_line = f"💰 К оплате: {charge_rubles} ₽ (акция −{discount_kopecks // 100} ₽)"
    elif discount_kopecks > 0:
        price_line = f"💰 К оплате: {charge_rubles} ₽ (скидка по промокоду −{discount_kopecks // 100} ₽)"
    else:
        price_line = f"💰 К оплате: {charge_rubles} ₽"
    text = (
        f"📋 Оформление подписки\n\n"
        f"🔹 Тариф: {display_name}\n"
        f"{price_line}\n\n"
        f"Нажмите кнопку ниже для перехода на страницу оплаты 👇"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "💳 Перейти к оплате", "url": result.confirmation_url}],
            [{"text": "↩️ Назад", "callback_data": CB_BUY_VPN}],
        ],
    }
    return text, keyboard


async def _process_cryptobot_payment(
    composition: Slice1Composition,
    uid: int | None,
    *,
    plan_id: str,
    device_count: int,
) -> tuple[str, dict[str, Any] | None]:
    """Create a CryptoBot fiat-RUB invoice for a subscription purchase (the crypto path).

    Mirrors ``_process_yookassa_payment``: same plan/price/promo resolution, but creates a
    CryptoBot invoice (user pays the crypto equivalent; RUB face value is fixed by us) and
    carries the full payment context in the invoice ``payload`` string. The CryptoBot webhook
    re-fetches the invoice and fulfills identically to a card payment (subscription apply,
    VLESS, referral commissions, promo redemption).
    """
    import logging
    import os

    from app.application.plan_pricing import charge_metadata_fields, resolve_effective_charge
    from app.cryptobot.client import CryptoBotClient
    from app.cryptobot.payload import encode_payment_context
    from app.domain.plans import calculate_total_price, plan_display_name
    from app.domain.pricing import WINNER_GLOBAL

    _logger = logging.getLogger(__name__)

    if uid is None:
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)

    pricing = get_plan_pricing(_get_pool_from_composition(composition))
    plan = await pricing.get_plan(plan_id)
    if plan is None:
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)

    total_rubles = calculate_total_price(plan, device_count)

    # CryptoBot carries the FULL context in its payload (no client-side injection).
    # Итог — best-of(промокод, глобальная): base_amount_kopecks фиксирует базу,
    # promo_code кладётся ТОЛЬКО при победе промокода (глобальная код не расходует).
    context: dict[str, object] = {
        "plan_id": plan_id,
        "device_count": str(device_count),
        "telegram_user_id": str(uid),
    }
    internal_user_id = await _get_internal_user_id(composition, uid)
    promo = None
    promo_code = None
    if internal_user_id is not None:
        context["internal_user_id"] = internal_user_id
        from app.application.promo_service import resolve_active_promo

        active = await resolve_active_promo(
            pool=_get_pool_from_composition(composition),
            promo_applied_repo=composition.promo_applied_repo,
            promo_code_repo=composition.promo_code_repo,
            promo_redemption_repo=composition.promo_redemption_repo,
            snapshots=composition.snapshots,
            internal_user_id=internal_user_id,
            base_rubles=total_rubles,
            plan_id=plan_id,
        )
        if active is not None:
            promo = active.promo
            promo_code = active.code
    global_discount = await pricing.get_global_discount()
    resolved = resolve_effective_charge(
        base_rubles=total_rubles,
        plan_id=plan_id,
        promo=promo,
        global_discount=global_discount,
    )
    charge_rubles = resolved.charge_rubles
    discount_kopecks = resolved.price.discount_kopecks
    context.update(charge_metadata_fields(resolved, promo_code=promo_code, global_discount=global_discount))

    client = CryptoBotClient.from_env()
    if client is None:
        _logger.warning("cryptobot not configured, cannot create bot payment")
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)

    return_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://bravada-connect.ru").strip().rstrip("/")
    ttl = int(os.environ.get("CRYPTOBOT_INVOICE_TTL_SECONDS", "3600"))
    try:
        result = await client.create_invoice(
            amount_rubles=charge_rubles,
            payload_str=encode_payment_context(context),
            description=f"Bravada VPN - {plan_display_name(plan_id)}",
            paid_btn_url=f"{return_url}/dashboard",
            expires_in=ttl,
        )
    except Exception:
        _logger.exception("cryptobot create_invoice failed in bot flow")
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)

    from app.bot_transport.payment_message_registry import set_pending_payment_for_user

    set_pending_payment_for_user(uid, result.invoice_id)

    display_name = plan_display_name(plan_id)
    if discount_kopecks > 0 and resolved.price.winner == WINNER_GLOBAL:
        price_line = f"💰 К оплате: {charge_rubles} ₽ (акция −{discount_kopecks // 100} ₽)"
    elif discount_kopecks > 0:
        price_line = f"💰 К оплате: {charge_rubles} ₽ (скидка по промокоду −{discount_kopecks // 100} ₽)"
    else:
        price_line = f"💰 К оплате: {charge_rubles} ₽"
    text = (
        "📋 Оформление подписки\n\n"
        f"🔹 Тариф: {display_name}\n"
        f"{price_line}\n\n"
        "Оплата криптой (USDT, TON и др.). Нажмите кнопку ниже для перехода к оплате 👇"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "₮ Перейти к оплате", "url": result.pay_url}],
            [{"text": "↩️ Назад", "callback_data": CB_BUY_VPN}],
        ],
    }
    return text, keyboard


async def _process_balance_payment(
    composition: Slice1Composition,
    uid: int | None,
    *,
    plan_id: str,
    device_count: int,
) -> tuple[str, dict[str, Any] | None]:
    # Debounce pre-filter (UX double-click). The service has server-side idempotency
    # too (advisory lock + description dedup), but this cheap in-process check avoids
    # a DB roundtrip on rapid double-clicks of the inline button.
    if uid is not None:
        now_ts = time.monotonic()
        last_ts = _balance_payment_last_click.get(uid)
        if last_ts is not None and now_ts - last_ts < _BALANCE_PAYMENT_DEBOUNCE_SECONDS:
            return text_balance_payment_in_progress(), back_only_keyboard(CB_MAIN_MENU)
        _balance_payment_last_click[uid] = now_ts
        _trim_inmemory_dict(_balance_payment_last_click)

    internal_user_id = await _get_internal_user_id(composition, uid)
    if internal_user_id is None:
        return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)

    from app.application.balance_payment_service import pay_plan_from_balance

    result = await pay_plan_from_balance(
        pool=_get_pool_from_composition(composition),
        composition=composition,
        internal_user_id=internal_user_id,
        plan_id=plan_id,
        device_count=device_count,
        description_prefix="balance payment",
        vless_provider=composition.vless_provider,
        notifier=None,  # M5-bot (notify referrers) — follow-up PR with notifier wiring
    )
    return await _map_balance_payment_result(result, composition, uid)


async def _map_balance_payment_result(
    result: Any,
    composition: Slice1Composition,
    uid: int | None,
) -> tuple[str, dict[str, Any] | None]:
    from app.application.balance_payment_service import BalancePaymentStatus

    s = result.status
    if s is BalancePaymentStatus.COMPLETED or s is BalancePaymentStatus.ALREADY_COMPLETED:
        active_until_str = result.active_until_utc.date().isoformat() if result.active_until_utc else None
        return text_balance_payment_success(active_until_str), await _main_menu_keyboard(composition, uid)
    if s is BalancePaymentStatus.INSUFFICIENT:
        return text_balance_insufficient(), back_only_keyboard(CB_BUY_VPN)
    if s is BalancePaymentStatus.IN_PROGRESS:
        return text_balance_payment_in_progress(), back_only_keyboard(CB_MAIN_MENU)
    if s is BalancePaymentStatus.INVALID_PLAN:
        return text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)
    # INVALID_DEVICE_COUNT, VLESS_FAILED, INTERNAL_ERROR → generic error.
    return text_error_generic(), back_only_keyboard(CB_MAIN_MENU)


# Referral-commission credit moved to balance_payment_service
# (_credit_commissions_best_effort / _credit_commissions_inmemory).


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

    duration_days = 30
    if snap and snap.plan_id:
        plan = get_plan(snap.plan_id)
        if plan is not None:
            duration_days = plan.duration_days

    cost = extra_device_cost(new_count, current, duration_days)

    balance_record = await composition.referral_balance_repo.get_balance(internal_user_id)
    balance_kopecks = balance_record.balance_kopecks if balance_record else 0

    text = text_add_device_confirm(current, new_count, duration_days=duration_days)
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
    if uid is None:
        return text_error_generic(), back_only_keyboard(CB_SETTINGS)

    internal_user_id = await _get_internal_user_id(composition, uid)
    if internal_user_id is None:
        return text_error_generic(), back_only_keyboard(CB_SETTINGS)

    from app.application.balance_payment_service import BalancePaymentStatus, add_device_from_balance

    result = await add_device_from_balance(
        pool=_get_pool_from_composition(composition),
        composition=composition,
        internal_user_id=internal_user_id,
        new_device_count=new_count,
        description_prefix="add device",
    )
    s = result.status
    if s is BalancePaymentStatus.COMPLETED or s is BalancePaymentStatus.ALREADY_COMPLETED:
        return text_add_device_success(new_count), back_only_keyboard(CB_MAIN_MENU)
    if s is BalancePaymentStatus.INSUFFICIENT:
        return text_balance_insufficient(), back_only_keyboard(CB_SETTINGS)
    if s is BalancePaymentStatus.NO_ACTIVE:
        return text_add_device_unavailable(), back_only_keyboard(CB_MAIN_MENU)
    if s is BalancePaymentStatus.NOT_HIGHER:
        snap = await composition.snapshots.get_for_user(internal_user_id)
        current = snap.device_count if snap and snap.device_count else DEVICES_DEFAULT
        return text_add_device_intro(current), add_device_select_keyboard(current)
    return text_error_generic(), back_only_keyboard(CB_SETTINGS)


async def _process_add_device_yookassa(
    composition: Slice1Composition,
    uid: int | None,
    new_count: int,
) -> tuple[str, dict[str, Any] | None]:
    """Create a real YooKassa payment for adding devices (the card path).

    Mirrors ``_process_yookassa_payment`` but for a device top-up: the amount is
    the extra-device cost (NOT a plan price), and the payment metadata carries
    ``kind=add_device`` so the webhook increases ``device_count`` without
    extending the subscription. Previously this button was a dead-end stub
    ("payment unavailable"); now it opens a real checkout like buying a plan.
    """
    import logging
    import os

    from app.domain.devices import extra_device_cost
    from app.domain.plans import get_plan
    from app.yookassa.client import YooKassaClient

    _logger = logging.getLogger(__name__)

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

    duration_days = 30
    if snap.plan_id:
        plan = get_plan(snap.plan_id)
        if plan is not None:
            duration_days = plan.duration_days

    cost_rubles = extra_device_cost(new_count, current, duration_days)
    if cost_rubles <= 0:
        return text_error_generic(), back_only_keyboard(CB_SETTINGS)

    # Применённый промокод: cost_rubles И expected_amount_kopecks скидываются синхронно,
    # т.к. add-device вебхук сверяет платёж именно по expected_amount_kopecks из metadata.
    charge_rubles = cost_rubles
    expected_amount_kopecks = cost_rubles * 100
    add_device_meta: dict[str, str] = {
        "kind": "add_device",
        "new_device_count": str(new_count),
        "expected_amount_kopecks": str(expected_amount_kopecks),
    }
    from app.application.promo_service import resolve_active_promo

    active = await resolve_active_promo(
        pool=_get_pool_from_composition(composition),
        promo_applied_repo=composition.promo_applied_repo,
        promo_code_repo=composition.promo_code_repo,
        promo_redemption_repo=composition.promo_redemption_repo,
        snapshots=composition.snapshots,
        internal_user_id=internal_user_id,
        base_rubles=cost_rubles,
        is_addon=True,
    )
    if active is not None:
        charge_rubles = active.price.discounted_rubles
        expected_amount_kopecks = active.price.discounted_kopecks
        add_device_meta["expected_amount_kopecks"] = str(expected_amount_kopecks)
        add_device_meta["discount_kopecks"] = str(active.price.discount_kopecks)
        add_device_meta["promo_code"] = active.code

    client = YooKassaClient.from_env()
    if client is None:
        _logger.warning("yookassa not configured, cannot create add-device payment")
        return text_payment_unavailable(), back_only_keyboard(CB_ADD_DEVICE)

    return_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://bravada-connect.ru").strip().rstrip("/")
    try:
        result = await client.create_payment(
            amount_rubles=charge_rubles,
            plan_id=snap.plan_id or "1m",
            device_count=new_count,
            telegram_user_id=uid,
            return_url=f"{return_url}/dashboard",
            description=f"Bravada VPN — добавление устройств ({current}→{new_count})",
            metadata=add_device_meta,
        )
    except Exception:
        _logger.exception("yookassa create_payment failed in add-device flow")
        return text_payment_unavailable(), back_only_keyboard(CB_ADD_DEVICE)

    from app.bot_transport.payment_message_registry import set_pending_payment_for_user

    set_pending_payment_for_user(uid, result.payment_id)

    if active is not None:
        price_line = f"💰 К оплате: {charge_rubles} ₽ (скидка по промокоду −{active.price.discount_kopecks // 100} ₽)"
    else:
        price_line = f"💰 К оплате: {charge_rubles} ₽"
    text = (
        "📋 Добавление устройств\n\n"
        f"📱 Устройств: {current} → {new_count}\n"
        f"{price_line}\n\n"
        "Нажмите кнопку ниже для перехода на страницу оплаты 👇"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "💳 Перейти к оплате", "url": result.confirmation_url}],
            [{"text": "↩️ Назад", "callback_data": CB_ADD_DEVICE}],
        ],
    }
    return text, keyboard


async def _process_add_device_cryptobot(
    composition: Slice1Composition,
    uid: int | None,
    new_count: int,
) -> tuple[str, dict[str, Any] | None]:
    """Create a CryptoBot invoice for adding devices (the crypto path).

    Mirrors ``_process_add_device_yookassa``: same cost/promo resolution, but creates a
    CryptoBot invoice carrying ``kind=add_device`` context in the payload. The webhook's
    add-device path raises ``device_count`` without extending the subscription.
    """
    import logging
    import os

    from app.cryptobot.client import CryptoBotClient
    from app.cryptobot.payload import encode_payment_context
    from app.domain.devices import extra_device_cost
    from app.domain.plans import get_plan

    _logger = logging.getLogger(__name__)

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

    duration_days = 30
    if snap.plan_id:
        plan = get_plan(snap.plan_id)
        if plan is not None:
            duration_days = plan.duration_days

    cost_rubles = extra_device_cost(new_count, current, duration_days)
    if cost_rubles <= 0:
        return text_error_generic(), back_only_keyboard(CB_SETTINGS)

    charge_rubles = cost_rubles
    expected_amount_kopecks = cost_rubles * 100
    context: dict[str, object] = {
        "kind": "add_device",
        "telegram_user_id": str(uid),
        "new_device_count": str(new_count),
        "expected_amount_kopecks": str(expected_amount_kopecks),
    }
    from app.application.promo_service import resolve_active_promo

    active = await resolve_active_promo(
        pool=_get_pool_from_composition(composition),
        promo_applied_repo=composition.promo_applied_repo,
        promo_code_repo=composition.promo_code_repo,
        promo_redemption_repo=composition.promo_redemption_repo,
        snapshots=composition.snapshots,
        internal_user_id=internal_user_id,
        base_rubles=cost_rubles,
        is_addon=True,
    )
    if active is not None:
        charge_rubles = active.price.discounted_rubles
        expected_amount_kopecks = active.price.discounted_kopecks
        context["expected_amount_kopecks"] = str(expected_amount_kopecks)
        context["discount_kopecks"] = str(active.price.discounted_kopecks)
        context["promo_code"] = active.code

    client = CryptoBotClient.from_env()
    if client is None:
        _logger.warning("cryptobot not configured, cannot create add-device payment")
        return text_payment_unavailable(), back_only_keyboard(CB_ADD_DEVICE)

    return_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://bravada-connect.ru").strip().rstrip("/")
    ttl = int(os.environ.get("CRYPTOBOT_INVOICE_TTL_SECONDS", "3600"))
    try:
        result = await client.create_invoice(
            amount_rubles=charge_rubles,
            payload_str=encode_payment_context(context),
            description=f"Bravada VPN - add devices ({current}->{new_count})",
            paid_btn_url=f"{return_url}/dashboard",
            expires_in=ttl,
        )
    except Exception:
        _logger.exception("cryptobot create_invoice failed in add-device flow")
        return text_payment_unavailable(), back_only_keyboard(CB_ADD_DEVICE)

    from app.bot_transport.payment_message_registry import set_pending_payment_for_user

    set_pending_payment_for_user(uid, result.invoice_id)

    if active is not None:
        price_line = f"💰 К оплате: {charge_rubles} ₽ (скидка по промокоду −{active.price.discounted_kopecks // 100} ₽)"
    else:
        price_line = f"💰 К оплате: {charge_rubles} ₽"
    text = (
        "📋 Добавление устройств\n\n"
        f"📱 Устройств: {current} → {new_count}\n"
        f"{price_line}\n\n"
        "Оплата криптой. Нажмите кнопку ниже для перехода к оплате 👇"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "₮ Перейти к оплате", "url": result.pay_url}],
            [{"text": "↩️ Назад", "callback_data": CB_ADD_DEVICE}],
        ],
    }
    return text, keyboard


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
    media_type: str | None = None
    media_path: str | None = None
    follow_up_main_menu: bool = False

    if code in (CB_MAIN_MENU, "store_menu"):
        # Trial is NOT auto-activated here — the user must click the trial button
        # (surfaced in the menu via _main_menu_keyboard while _is_trial_available is True).
        text, keyboard = await _main_menu_text(composition, uid), await _main_menu_keyboard(composition, uid)

    elif code in (CB_BUY_VPN, "store_plans"):
        # Витрина тарифов — эффективные цены (админ-оверрайды) + глобальная скидка.
        pricing = get_plan_pricing(_get_pool_from_composition(composition))
        plans = plan_options_from_plans(await pricing.get_fixed_plans())
        global_discount = await pricing.get_global_discount()
        applied = await _active_applied_promo(composition, uid)
        promo = applied[0] if applied is not None else None
        text, keyboard = (
            text_buy_vpn_intro(promo=promo, global_discount=global_discount),
            buy_vpn_keyboard(plans, promo=promo, global_discount=global_discount),
        )

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

    elif code.startswith(CB_BROADCAST_DAYS):
        # Бонус-кнопка из рассылки: "bc_days:{job_id}". Параметры бонуса (дни) хранятся
        # в broadcast_bonus_claims, не в callback. job_id определяет, какой бонус выдать.
        job_id = code[len(CB_BROADCAST_DAYS) :]
        text, keyboard = await _handle_broadcast_days_claim(composition, uid, job_id)

    elif code.startswith(CB_BROADCAST_PROMO):
        # Кнопка промокода из рассылки: "bc_promo:{job_id}". Код хранится в
        # broadcast_promo_claims, не в callback (приватность + 64-байт лимит).
        job_id = code[len(CB_BROADCAST_PROMO) :]
        text, keyboard = await _handle_broadcast_promo_claim(composition, uid, job_id)

    elif code in (CB_MY_SUB, "store_success", "store_success_active"):
        text, keyboard = await _render_subscription_status(composition, uid, cid)

    elif code == CB_CONNECT_DEVICE:
        if await _has_active_subscription(composition, uid):
            text, keyboard = text_connect_device(), connect_device_keyboard()
        else:
            text, keyboard = text_no_subscription(), no_subscription_keyboard()

    elif code == CB_CONNECT_WIN:
        text = win_step_1_text()
        keyboard = win_step_1_keyboard()
        media_type = "document"
        media_path = "media/karing_windows_x64.exe"

    elif code == CB_CONNECT_ANDROID:
        text = text_android_app_chooser()
        keyboard = android_app_chooser_keyboard()

    elif code == CB_CONNECT_IOS:
        text = text_ios_app_chooser()
        keyboard = ios_app_chooser_keyboard()

    elif code == CB_CONNECT_MAC:
        text = text_mac_app_chooser()
        keyboard = mac_app_chooser_keyboard()

    elif code == CB_CONNECT_TV:
        text = tv_step_1_text()
        keyboard = tv_step_1_keyboard()

    elif code.startswith(
        (
            CB_IOS_STEP,
            CB_MAC_STEP,
            CB_TV_STEP,
            CB_WIN_STEP,
            CB_ANDROID_STEP,
            CB_IOS_HAPP_STEP,
            CB_MAC_HAPP_STEP,
            CB_ANDROID_HAPP_STEP,
        )
    ):
        text, keyboard, media_type, media_path = await _render_platform_step(
            code,
            uid,
            composition,
        )

    elif code in (CB_IOS_DID_WORK, CB_MAC_DID_WORK, CB_TV_DID_WORK, CB_WIN_DID_WORK, CB_ANDROID_DID_WORK):
        text = ios_did_work_text()
        keyboard = _did_work_kb_for(code)

    elif code in (CB_IOS_YES, CB_MAC_YES, CB_TV_YES, CB_WIN_YES, CB_ANDROID_YES):
        text = ios_success_text()
        keyboard = await _main_menu_keyboard(composition, uid)

    elif code in (CB_IOS_NO, CB_MAC_NO, CB_TV_NO, CB_WIN_NO, CB_ANDROID_NO):
        text = ios_problem_text()
        keyboard = _problem_kb_for(code)

    elif code in (CB_IOS_RETRY, CB_MAC_RETRY, CB_TV_RETRY, CB_WIN_RETRY, CB_ANDROID_RETRY):
        text, keyboard = _step_1_for_retry(code)

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
                    text = text_my_keys_menu(subscription_url=vless_result.config.subscription_url)
                    keyboard = my_keys_menu_keyboard(vless_result.config.servers)
                else:
                    text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)
            else:
                text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)
        else:
            text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MAIN_MENU)

    elif code == CB_ALL_KEYS:
        has_sub = await _has_active_subscription(composition, uid)
        if has_sub and uid is not None and composition.vless_provider is not None:
            from app.issuance.vless_provider import VlessProviderOutcome

            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                vless_result = await composition.vless_provider.get_user_config(
                    internal_user_id=id_rec.internal_user_id,
                )
                if vless_result.outcome == VlessProviderOutcome.SUCCESS and vless_result.config is not None:
                    text = text_all_keys_list(vless_result.config)
                    keyboard = all_keys_list_keyboard()
                else:
                    text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MY_KEYS)
            else:
                text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MY_KEYS)
        else:
            text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MY_KEYS)

    elif code.startswith(CB_SERVER):
        server_label = code[len(CB_SERVER) :]
        has_sub = await _has_active_subscription(composition, uid)
        if has_sub and uid is not None and composition.vless_provider is not None:
            from app.issuance.vless_provider import VlessProviderOutcome

            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                vless_result = await composition.vless_provider.get_user_config(
                    internal_user_id=id_rec.internal_user_id,
                )
                if vless_result.outcome == VlessProviderOutcome.SUCCESS and vless_result.config is not None:
                    server = None
                    for s in vless_result.config.servers:
                        if s.server_label == server_label:
                            server = s
                            break
                    if server is not None:
                        text = text_single_server_key(server)
                        keyboard = single_server_key_keyboard()
                    else:
                        text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MY_KEYS)
                else:
                    text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MY_KEYS)
            else:
                text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MY_KEYS)
        else:
            text, keyboard = text_keys_not_available(), back_only_keyboard(CB_MY_KEYS)

    elif code == CB_REISSUE_KEYS:
        text, keyboard = text_reissue_confirm(), reissue_confirm_keyboard()

    elif code == CB_REISSUE_CONFIRM:
        if uid is not None and composition.vless_provider is not None:
            from app.issuance.vless_provider import VlessProviderOutcome

            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                # Step 1: Revoke FIRST (while old UUID still exists in DB)
                # so the provider can find and disable/delete old keys on panels.
                await composition.vless_provider.revoke_user(internal_user_id=id_rec.internal_user_id)
                # Step 2: Clear stored UUID so create_user generates a fresh random key
                pool = _get_pool_from_composition(composition)
                if pool is not None:
                    await pool.execute(
                        "UPDATE user_identities SET vless_uuid = NULL WHERE internal_user_id = $1",
                        id_rec.internal_user_id,
                    )
                # Step 3: Create new keys with a fresh UUID
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
            from app.shared.site_url import get_site_base_url

            id_rec = await composition.identity.find_by_telegram_user_id(uid)
            if id_rec is not None:
                info = await get_referral_info(
                    internal_user_id=id_rec.internal_user_id,
                    code_repo=composition.referral_code_repo,
                    balance_repo=composition.referral_balance_repo,
                    relationship_repo=composition.referral_relationship_repo,
                    transaction_repo=composition.referral_transaction_repo,
                    bonus_repo=composition.referral_bonus_repo,
                    bot_username=composition.bot_username,
                    site_base_url=get_site_base_url(),
                )
                text, keyboard = text_referral_program(info), back_only_keyboard(CB_MAIN_MENU)
            else:
                text, keyboard = (
                    text_referral_program(
                        ReferralInfo(
                            referral_code="",
                            referral_link="",
                            web_referral_link="",
                            balance_rubles=0.0,
                            l1_invited=0,
                            l2_invited=0,
                            total_earned_rubles=0.0,
                            bonus_days_awarded=0,
                        )
                    ),
                    back_only_keyboard(CB_MAIN_MENU),
                )
        else:
            from app.application.referral_handler import ReferralInfo

            text, keyboard = (
                text_referral_program(
                    ReferralInfo(
                        referral_code="",
                        referral_link="",
                        web_referral_link="",
                        balance_rubles=0.0,
                        l1_invited=0,
                        l2_invited=0,
                        total_earned_rubles=0.0,
                        bonus_days_awarded=0,
                    )
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

    elif code == CB_CUSTOM_DAYS:
        if uid is not None:
            _awaiting_custom_days[uid] = True
            _trim_inmemory_dict(_awaiting_custom_days)
        text = text_custom_days_prompt()
        keyboard = custom_days_prompt_keyboard()

    elif code == CB_PROMOCODE or code.startswith(CB_PROMO_AT):
        # Storefront-колбэки обходят командный лимитер, поэтому флаг FSM ставим здесь,
        # а сам rate-limit ввода текстом — в _handle_promo_code_input (ключ PROMOCODE).
        # Значение флага = callback возврата: из меню — в меню, из оплаты — к сводке заказа.
        if code.startswith(CB_PROMO_AT):
            return_target = f"{CB_CONFIRM_PAY}{code[len(CB_PROMO_AT) :]}"
            back_callback = return_target
        else:
            return_target = CB_MAIN_MENU
            back_callback = CB_MAIN_MENU
        if uid is not None:
            _awaiting_promo_code[uid] = return_target
            _trim_inmemory_dict(_awaiting_promo_code)
        text = text_promo_prompt()
        keyboard = promo_prompt_keyboard(back_callback=back_callback)

    elif code.startswith(CB_PLAN):
        plan_id = code[len(CB_PLAN) :]
        pricing = get_plan_pricing(_get_pool_from_composition(composition))
        plan = await pricing.get_plan(plan_id)
        if plan is not None:
            applied = await _active_applied_promo(composition, uid)
            promo = applied[0] if applied is not None else None
            text = text_device_select(
                plan_id,
                plan.price_rubles,
                plan.duration_days,
                DEVICES_DEFAULT,
                promo=promo,
                global_discount=await pricing.get_global_discount(),
            )
            keyboard = device_select_keyboard(plan_id, DEVICES_DEFAULT)

    elif code.startswith(CB_DEVICES):
        rest = code[len(CB_DEVICES) :]
        # plan_id may contain colons (e.g. "custom:45"), device_count is always last
        sep = rest.rfind(":")
        if sep >= 0:
            plan_id = rest[:sep]
            try:
                device_count = int(rest[sep + 1 :])
            except ValueError:
                device_count = DEVICES_DEFAULT
        else:
            plan_id = rest
            device_count = DEVICES_DEFAULT
        pricing = get_plan_pricing(_get_pool_from_composition(composition))
        plan = await pricing.get_plan(plan_id)
        if plan is not None:
            applied = await _active_applied_promo(composition, uid)
            promo = applied[0] if applied is not None else None
            text = text_device_select(
                plan_id,
                plan.price_rubles,
                plan.duration_days,
                device_count,
                promo=promo,
                global_discount=await pricing.get_global_discount(),
            )
            keyboard = device_select_keyboard(plan_id, device_count)

    elif code.startswith(CB_CONFIRM_PAY):
        rest = code[len(CB_CONFIRM_PAY) :]
        sep = rest.rfind(":")
        if sep >= 0:
            plan_id = rest[:sep]
            try:
                device_count = int(rest[sep + 1 :])
            except ValueError:
                device_count = DEVICES_DEFAULT
        else:
            plan_id = rest
            device_count = DEVICES_DEFAULT
        text, keyboard = await _render_purchase_summary(composition, uid, plan_id, device_count)

    elif code.startswith(CB_DO_PAY):
        rest = code[len(CB_DO_PAY) :]
        sep = rest.rfind(":")
        if sep >= 0:
            plan_id = rest[:sep]
            try:
                device_count = int(rest[sep + 1 :])
            except ValueError:
                device_count = DEVICES_DEFAULT
        else:
            plan_id = rest
            device_count = DEVICES_DEFAULT
        text, keyboard = await _process_yookassa_payment(
            composition,
            uid,
            plan_id=plan_id,
            device_count=device_count,
        )

    elif code.startswith(CB_DO_PAY_CRYPTO):
        rest = code[len(CB_DO_PAY_CRYPTO) :]
        sep = rest.rfind(":")
        if sep >= 0:
            plan_id = rest[:sep]
            try:
                device_count = int(rest[sep + 1 :])
            except ValueError:
                device_count = DEVICES_DEFAULT
        else:
            plan_id = rest
            device_count = DEVICES_DEFAULT
        try:
            text, keyboard = await _process_cryptobot_payment(
                composition,
                uid,
                plan_id=plan_id,
                device_count=device_count,
            )
        except Exception:
            _LOGGER.exception("cryptobot _process_cryptobot_payment failed plan=%s", plan_id)
            text, keyboard = text_payment_unavailable(), back_only_keyboard(CB_BUY_VPN)

    elif code.startswith(CB_PAY_BALANCE):
        rest = code[len(CB_PAY_BALANCE) :]
        sep = rest.rfind(":")
        if sep >= 0:
            plan_id = rest[:sep]
            try:
                device_count = int(rest[sep + 1 :])
            except ValueError:
                device_count = DEVICES_DEFAULT
        else:
            plan_id = rest
            device_count = DEVICES_DEFAULT
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
            try:
                new_count = int(remainder.split(":")[1])
                text, keyboard = await _process_add_device_yookassa(composition, uid, new_count)
            except (ValueError, TypeError):
                text = text_error_generic()
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
        new_count_str = code[len("add_dev_pay:") :]
        try:
            new_count = int(new_count_str)
            text, keyboard = await _process_add_device_yookassa(composition, uid, new_count)
        except (ValueError, TypeError):
            text = text_error_generic()
            keyboard = back_only_keyboard(CB_SETTINGS)

    elif code.startswith("add_dev_pay_crypto:"):
        new_count_str = code[len("add_dev_pay_crypto:") :]
        try:
            new_count = int(new_count_str)
            text, keyboard = await _process_add_device_cryptobot(composition, uid, new_count)
        except (ValueError, TypeError):
            text = text_error_generic()
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

    parse_mode = "HTML"
    # Connection step initial renders (CB_CONNECT_*) also use HTML step texts
    if code in (CB_CONNECT_IOS, CB_CONNECT_MAC, CB_CONNECT_TV, CB_CONNECT_WIN, CB_CONNECT_ANDROID):
        parse_mode = "HTML"
    # iOS/Mac/TV/Win/Android steps use HTML for bold headers and code formatting
    if code.startswith(CB_IOS_STEP) or code == CB_IOS_RETRY:
        parse_mode = "HTML"
    if code.startswith(CB_MAC_STEP) or code == CB_MAC_RETRY:
        parse_mode = "HTML"
    if code.startswith(CB_TV_STEP) or code == CB_TV_RETRY:
        parse_mode = "HTML"
    if code.startswith(CB_WIN_STEP) or code == CB_WIN_RETRY:
        parse_mode = "HTML"
    if code.startswith(CB_ANDROID_STEP) or code == CB_ANDROID_RETRY:
        parse_mode = "HTML"
    # Happ steps (ios/mac/android) use HTML for bold headers and the <code> URL block
    if code.startswith((CB_IOS_HAPP_STEP, CB_MAC_HAPP_STEP, CB_ANDROID_HAPP_STEP)):
        parse_mode = "HTML"
    if follow_up_main_menu:
        return RenderedMessagePackage(
            message_text=text,
            action_keys=(),
            correlation_id=cid,
            reply_markup=keyboard,
            replay_suppresses_outbound=transport.replay_suppresses_outbound,
            uc01_idempotency_key=transport.uc01_idempotency_key,
            parse_mode=parse_mode,
            media_type=media_type,
            media_path=media_path,
            follow_up_messages=(
                RenderedMessagePackage(
                    message_text=text_main_menu(),
                    action_keys=(),
                    correlation_id=cid + "_menu",
                    reply_markup=await _main_menu_keyboard(composition, uid),
                ),
            ),
        )

    return RenderedMessagePackage(
        message_text=text,
        action_keys=(),
        correlation_id=cid,
        reply_markup=keyboard,
        replay_suppresses_outbound=transport.replay_suppresses_outbound,
        uc01_idempotency_key=transport.uc01_idempotency_key,
        parse_mode=parse_mode,
        media_type=media_type,
        media_path=media_path,
    )


# ─── Main facade function
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
                # Handle custom days input
                if _awaiting_custom_days.get(uid, False):
                    custom_text, custom_keyboard, is_valid = _handle_custom_days_input(uid, user_text.strip())
                    if is_valid:
                        _awaiting_custom_days.pop(uid, None)
                    return RenderedMessagePackage(
                        message_text=custom_text,
                        action_keys=(),
                        correlation_id=correlation_id or uuid.uuid4().hex,
                        reply_markup=custom_keyboard,
                        replay_suppresses_outbound=False,
                        uc01_idempotency_key=None,
                    )
                if _awaiting_promo_code.get(uid):
                    # Флаг сбрасывается только при успешном применении (внутри обработчика) —
                    # как в custom_days, чтобы опечатку можно было сразу исправить новым сообщением.
                    # Значение флага = callback возврата (меню либо сводка заказа).
                    promo_text, promo_keyboard = await _handle_promo_code_input(
                        composition, uid, user_text.strip(), _awaiting_promo_code[uid]
                    )
                    # Сводка заказа (возврат из оплаты) содержит HTML-теги (<b>…</b>), поэтому
                    # явный parse_mode HTML — иначе теги отдаются literal-текстом. Прочие тексты
                    # promo-обработчика HTML-безопасны; пользовательский ввод экранируется
                    # в text_promo_code_invalid.
                    return RenderedMessagePackage(
                        message_text=promo_text,
                        action_keys=(),
                        correlation_id=correlation_id or uuid.uuid4().hex,
                        reply_markup=promo_keyboard,
                        replay_suppresses_outbound=False,
                        uc01_idempotency_key=None,
                        parse_mode="HTML",
                    )
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

    # Short-circuit "noop" callbacks (non-clickable label buttons) — no message sent, spinner dismissed.
    is_callback = isinstance(update.get("callback_query"), Mapping)
    if is_callback:
        cq = update.get("callback_query")
        if isinstance(cq, Mapping) and cq.get("data") == "noop":
            return RenderedMessagePackage(
                message_text="",
                action_keys=(),
                correlation_id=correlation_id or uuid.uuid4().hex,
                reply_markup=None,
                replay_suppresses_outbound=False,
                uc01_idempotency_key=None,
            )

    transport = await handle_slice1_telegram_update(
        update,
        composition,
        correlation_id=correlation_id,
    )

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
