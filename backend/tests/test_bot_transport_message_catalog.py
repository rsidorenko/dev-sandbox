"""Pure tests for slice-1 message catalog rendering (no SDK, no network)."""

from __future__ import annotations

import re

import pytest

from app.bot_transport.message_catalog import (
    RenderedMessagePackage,
    render_telegram_outbound_plan,
)
from app.bot_transport.outbound import (
    OutboundKeyboardMarker,
    OutboundMessageKey,
    OutboundNextActionKey,
    OutboundPlanCategory,
    TelegramOutboundPlan,
)
from tests.slice1_expected_user_copy import (
    IDENTITY_READY_TEXT,
    INACTIVE_OR_NOT_ELIGIBLE_TEXT,
    NEEDS_ONBOARDING_TEXT,
    RESEND_ACCESS_ACCEPTED_TEXT,
    RESEND_ACCESS_COOLDOWN_TEXT,
    RESEND_ACCESS_NOT_ELIGIBLE_TEXT,
    RESEND_ACCESS_NOT_ENABLED_TEXT,
    RESEND_ACCESS_NOT_READY_TEXT,
    RESEND_ACCESS_TEMPORARILY_UNAVAILABLE_TEXT,
    SLICE1_HELP_TEXT,
    TELEGRAM_COMMAND_RATE_LIMITED_TEXT,
)

_CID = "corr-test-01"

# Block accidental DSN, secrets, and markup; catalog may describe feature limits (no false claims of payment).
_DSN_OR_SECRETISH = re.compile(
    r"(postgresql://|postgres://|ghp_[a-z0-9]+|sk_live_|sk_test_|" r"webhook|password\s*=|dsn=)",
    re.IGNORECASE,
)

# Doc 35 §G — instruction-class / secret / config / internal leak markers (lowercase needles).
# Deliberately excludes plain English "instructions" used in coarse/redacted copy.
_DOC35_G_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "-----begin",
    "private key",
    "-----end",
    "database_url",
    "postgres://",
    "postgresql://",
    "bearer ",
    "token=",
    "api_key",
    "secret=",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "raw_provider_payload",
    "config_payload",
    "private_key",
    "traceback",
    "runtimeerror",
    "exception:",
)


def _assert_no_doc35_g_instruction_class_material(text: str) -> None:
    lowered = text.lower()
    for needle in _DOC35_G_FORBIDDEN_SUBSTRINGS:
        assert needle not in lowered, f"catalog text must not contain forbidden fragment {needle!r} (doc 35 §G)"


def _plan(
    *,
    category: OutboundPlanCategory,
    message_key: str,
    next_action_key: str | None = None,
    keyboard_marker: str = OutboundKeyboardMarker.NONE.value,
    correlation_id: str = _CID,
    replay_suppresses_outbound: bool = False,
    active_until_ymd: str | None = None,
) -> TelegramOutboundPlan:
    return TelegramOutboundPlan(
        category=category,
        message_key=message_key,
        next_action_key=next_action_key,
        keyboard_marker=keyboard_marker,
        correlation_id=correlation_id,
        replay_suppresses_outbound=replay_suppresses_outbound,
        active_until_ymd=active_until_ymd,
    )


def _assert_no_dsn_or_secretish(text: str) -> None:
    assert _DSN_OR_SECRETISH.search(text) is None, "unexpected DSN, credential, or transport leak"
    assert "postgresql" not in text.lower()
    assert "internal_user" not in text.lower()


def _assert_plain_text_no_markup(text: str) -> None:
    assert "<" not in text
    assert ">" not in text
    assert "`" not in text
    assert "[" not in text and "]" not in text


@pytest.fixture
def identity_ready_plan() -> TelegramOutboundPlan:
    return _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.IDENTITY_READY.value,
    )


def test_bootstrap_success_stable_text(identity_ready_plan: TelegramOutboundPlan) -> None:
    out = render_telegram_outbound_plan(identity_ready_plan)
    assert isinstance(out, RenderedMessagePackage)
    assert out.message_text == IDENTITY_READY_TEXT
    assert out.action_keys == ()
    assert out.correlation_id == _CID
    _assert_no_dsn_or_secretish(out.message_text)
    _assert_plain_text_no_markup(out.message_text)


def test_bootstrap_replay_same_as_success(identity_ready_plan: TelegramOutboundPlan) -> None:
    """Same catalog keys render the same text; default plan has no outbound suppress flag."""
    first = render_telegram_outbound_plan(identity_ready_plan)
    replay = render_telegram_outbound_plan(identity_ready_plan)
    assert first == replay
    assert first.replay_suppresses_outbound is False


def test_render_passes_replay_suppress_flag_without_changing_identity_ready_text() -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.IDENTITY_READY.value,
        replay_suppresses_outbound=True,
    )
    out = render_telegram_outbound_plan(plan)
    assert out.message_text == IDENTITY_READY_TEXT
    assert out.replay_suppresses_outbound is True


def test_onboarding_with_action_key() -> None:
    plan = _plan(
        category=OutboundPlanCategory.GUIDANCE,
        message_key=OutboundMessageKey.NEEDS_ONBOARDING.value,
        next_action_key=OutboundNextActionKey.COMPLETE_BOOTSTRAP.value,
        keyboard_marker=OutboundKeyboardMarker.PRIMARY_ONBOARDING.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert out.action_keys == (OutboundNextActionKey.COMPLETE_BOOTSTRAP.value,)
    assert out.message_text == NEEDS_ONBOARDING_TEXT
    _assert_no_dsn_or_secretish(out.message_text)


def test_onboarding_without_action_key() -> None:
    plan = _plan(
        category=OutboundPlanCategory.GUIDANCE,
        message_key=OutboundMessageKey.NEEDS_ONBOARDING.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert out.action_keys == ()


def test_inactive_not_eligible_fail_closed() -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.INACTIVE_OR_NOT_ELIGIBLE.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert out.message_text == INACTIVE_OR_NOT_ELIGIBLE_TEXT
    assert out.action_keys == ()
    _assert_no_dsn_or_secretish(out.message_text)


def test_needs_review_safe_no_internals() -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.NEEDS_REVIEW.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert "проверк" in out.message_text.lower()
    assert "ticket" not in out.message_text.lower()
    assert "internal" not in out.message_text.lower()
    _assert_no_dsn_or_secretish(out.message_text)


def test_subscription_active_confirmation_copy_safe() -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.SUBSCRIPTION_ACTIVE_CONFIRMATION.value,
        keyboard_marker=OutboundKeyboardMarker.FULFILLMENT_SUCCESS.value,
        active_until_ymd="2026-12-31",
    )
    out = render_telegram_outbound_plan(plan)
    lowered = out.message_text.lower()
    assert "активн" in lowered
    assert "2026-12-31" in out.message_text
    for needle in ("token", "secret", "reference", "signature"):
        assert needle not in lowered
    assert "/get_access" in out.message_text
    assert "/menu" in out.message_text
    _assert_no_dsn_or_secretish(out.message_text)
    _assert_plain_text_no_markup(out.message_text)


def test_subscription_active_renders_without_billing_internals() -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.SUBSCRIPTION_ACTIVE.value,
        active_until_ymd="2026-05-27",
    )
    out = render_telegram_outbound_plan(plan)
    assert "подписк" in out.message_text.lower() and "активн" in out.message_text.lower()
    assert "provider" not in out.message_text.lower()
    _assert_no_dsn_or_secretish(out.message_text)


def test_subscription_expired_copy_contains_renew_cta() -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.SUBSCRIPTION_EXPIRED.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert "истекл" in out.message_text.lower()
    assert "/renew" in out.message_text


@pytest.mark.parametrize(
    "message_key",
    (
        OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY.value,
        OutboundMessageKey.SUBSCRIPTION_ACTIVE_ACCESS_READY.value,
    ),
)
def test_subscription_access_readiness_messages_are_safe(
    message_key: str,
) -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=message_key,
        active_until_ymd="2026-05-27",
    )
    out = render_telegram_outbound_plan(plan)
    assert "активн" in out.message_text.lower()
    assert "/get_access" in out.message_text
    _assert_no_dsn_or_secretish(out.message_text)


def test_invalid_input_safe() -> None:
    plan = _plan(
        category=OutboundPlanCategory.ERROR,
        message_key=OutboundMessageKey.INVALID_INPUT.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert "некоррект" in out.message_text.lower()
    _assert_no_dsn_or_secretish(out.message_text)


def test_try_again_later_retry_safe() -> None:
    plan = _plan(
        category=OutboundPlanCategory.ERROR,
        message_key=OutboundMessageKey.TRY_AGAIN_LATER.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert "попробуйте позже" in out.message_text.lower()
    _assert_no_dsn_or_secretish(out.message_text)


def test_service_unavailable_generic() -> None:
    plan = _plan(
        category=OutboundPlanCategory.ERROR,
        message_key=OutboundMessageKey.SERVICE_UNAVAILABLE.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert "временно недоступен" in out.message_text.lower()
    _assert_no_dsn_or_secretish(out.message_text)


def test_error_telegram_command_rate_limited_render() -> None:
    plan = _plan(
        category=OutboundPlanCategory.ERROR,
        message_key=OutboundMessageKey.TELEGRAM_COMMAND_RATE_LIMITED.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert out.message_text == TELEGRAM_COMMAND_RATE_LIMITED_TEXT
    _assert_no_dsn_or_secretish(out.message_text)


def test_unknown_message_key_fail_closed_outage() -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key="totally_unknown_catalog_key",
    )
    out = render_telegram_outbound_plan(plan)
    assert out.message_text == ("Сервис временно недоступен. Пожалуйста, попробуйте позже.")
    assert out.action_keys == ()


def test_correlation_id_preserved_across_entries() -> None:
    cid = "trace-abc-999"
    for mk in (
        OutboundMessageKey.IDENTITY_READY.value,
        OutboundMessageKey.INVALID_INPUT.value,
        OutboundMessageKey.SERVICE_UNAVAILABLE.value,
    ):
        p = _plan(
            category=OutboundPlanCategory.SUCCESS,
            message_key=mk,
            correlation_id=cid,
        )
        assert render_telegram_outbound_plan(p).correlation_id == cid


def test_help_message_render() -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.SLICE1_HELP.value,
        keyboard_marker=OutboundKeyboardMarker.STOREFRONT_MAIN.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert out.message_text == SLICE1_HELP_TEXT
    assert "\n" in out.message_text
    assert "/start" in out.message_text
    _assert_no_dsn_or_secretish(out.message_text)
    _assert_plain_text_no_markup(out.message_text)
    assert out.reply_markup is not None
    assert "keyboard" in out.reply_markup


def test_catalog_outputs_cover_secret_and_markup_policy() -> None:
    """Predefined catalog strings avoid DSN/secret-style fragments and HTML-ish markup."""
    # Keep deterministic output independent from developer machine env.
    import os

    for k in (
        "TELEGRAM_STOREFRONT_PLAN_NAME",
        "TELEGRAM_STOREFRONT_PLAN_PRICE",
        "TELEGRAM_STOREFRONT_CHECKOUT_URL",
        "TELEGRAM_STOREFRONT_RENEWAL_URL",
        "TELEGRAM_STOREFRONT_SUPPORT_URL",
        "TELEGRAM_STOREFRONT_SUPPORT_HANDLE",
    ):
        os.environ.pop(k, None)
    for mk in OutboundMessageKey:
        p = _plan(
            category=OutboundPlanCategory.SUCCESS,
            message_key=mk.value,
        )
        text = render_telegram_outbound_plan(p).message_text
        _assert_no_dsn_or_secretish(text)
        _assert_plain_text_no_markup(text)


@pytest.mark.parametrize(
    ("message_key", "expected_text"),
    (
        (OutboundMessageKey.RESEND_ACCESS_ACCEPTED.value, RESEND_ACCESS_ACCEPTED_TEXT),
        (OutboundMessageKey.RESEND_ACCESS_NOT_ENABLED.value, RESEND_ACCESS_NOT_ENABLED_TEXT),
        (OutboundMessageKey.RESEND_ACCESS_NOT_ELIGIBLE.value, RESEND_ACCESS_NOT_ELIGIBLE_TEXT),
        (OutboundMessageKey.RESEND_ACCESS_COOLDOWN.value, RESEND_ACCESS_COOLDOWN_TEXT),
        (OutboundMessageKey.RESEND_ACCESS_NOT_READY.value, RESEND_ACCESS_NOT_READY_TEXT),
        (
            OutboundMessageKey.RESEND_ACCESS_TEMPORARILY_UNAVAILABLE.value,
            RESEND_ACCESS_TEMPORARILY_UNAVAILABLE_TEXT,
        ),
    ),
)
def test_resend_access_catalog_strings_are_safe(message_key: str, expected_text: str) -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=message_key,
    )
    out = render_telegram_outbound_plan(plan)
    assert out.message_text == expected_text
    lowered = out.message_text.lower()
    assert "issuance-ref" not in lowered
    assert "private key" not in lowered
    _assert_no_dsn_or_secretish(out.message_text)


def test_all_catalog_messages_forbid_doc35_g_instruction_class_material() -> None:
    """Contract: every catalog entry stays within doc 35 §G (no instruction-class delivery material)."""
    import os

    for k in (
        "TELEGRAM_STOREFRONT_PLAN_NAME",
        "TELEGRAM_STOREFRONT_PLAN_PRICE",
        "TELEGRAM_STOREFRONT_CHECKOUT_URL",
        "TELEGRAM_STOREFRONT_RENEWAL_URL",
        "TELEGRAM_STOREFRONT_SUPPORT_URL",
        "TELEGRAM_STOREFRONT_SUPPORT_HANDLE",
    ):
        os.environ.pop(k, None)
    for mk in OutboundMessageKey:
        plan = _plan(
            category=OutboundPlanCategory.SUCCESS,
            message_key=mk.value,
        )
        text = render_telegram_outbound_plan(plan).message_text
        _assert_no_doc35_g_instruction_class_material(text)
        _assert_no_dsn_or_secretish(text)
        _assert_plain_text_no_markup(text)
    assert "инструкци" in RESEND_ACCESS_ACCEPTED_TEXT.lower()


def test_storefront_plans_fallback_copy(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_STOREFRONT_PLAN_NAME", raising=False)
    monkeypatch.delenv("TELEGRAM_STOREFRONT_PLAN_PRICE", raising=False)
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.STORE_PLANS.value,
        keyboard_marker=OutboundKeyboardMarker.STOREFRONT_MAIN.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert "стоимость указана при оформлении" in out.message_text.lower()
    assert out.reply_markup is not None


def test_storefront_plans_uses_configured_name_and_price(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_STOREFRONT_PLAN_NAME", "VPN Monthly")
    monkeypatch.setenv("TELEGRAM_STOREFRONT_PLAN_PRICE", "$9.99")
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.STORE_PLANS.value,
        keyboard_marker=OutboundKeyboardMarker.STOREFRONT_MAIN.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert "VPN Monthly" in out.message_text
    assert "$9.99" in out.message_text


def test_storefront_buy_missing_or_invalid_checkout_fails_closed(monkeypatch) -> None:
    plan = _plan(category=OutboundPlanCategory.SUCCESS, message_key=OutboundMessageKey.STORE_BUY.value)
    monkeypatch.delenv("TELEGRAM_STOREFRONT_CHECKOUT_URL", raising=False)
    out_missing = render_telegram_outbound_plan(plan)
    assert out_missing.message_text == "Оплата пока не настроена, обратитесь в поддержку."
    monkeypatch.setenv("TELEGRAM_STOREFRONT_CHECKOUT_URL", "http://example.com/pay")
    out_invalid = render_telegram_outbound_plan(plan)
    assert out_invalid.message_text == "Оплата пока не настроена, обратитесь в поддержку."
    monkeypatch.setenv("TELEGRAM_STOREFRONT_CHECKOUT_URL", "https://example.com/pay?signature=abc")
    monkeypatch.setenv("TELEGRAM_CHECKOUT_REFERENCE_SECRET", "Checkout_Secret_1234567890_ABC")
    out_unsafe_query = render_telegram_outbound_plan(plan, telegram_user_id=12345)
    assert out_unsafe_query.message_text == "Оплата пока не настроена, обратитесь в поддержку."


def test_storefront_buy_uses_valid_checkout_url(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_STOREFRONT_CHECKOUT_URL", "https://example.com/checkout")
    monkeypatch.setenv("TELEGRAM_CHECKOUT_REFERENCE_SECRET", "Checkout_Secret_1234567890_ABC")
    plan = _plan(category=OutboundPlanCategory.SUCCESS, message_key=OutboundMessageKey.STORE_BUY.value)
    out = render_telegram_outbound_plan(plan, telegram_user_id=123456789)
    assert "https://example.com/checkout" in out.message_text
    assert "client_reference_id=" in out.message_text
    assert "client_reference_proof=" in out.message_text


def test_storefront_success_safe_pending_copy() -> None:
    plan = _plan(category=OutboundPlanCategory.SUCCESS, message_key=OutboundMessageKey.STORE_SUCCESS.value)
    out = render_telegram_outbound_plan(plan)
    lowered = out.message_text.lower()
    assert "оплата получен" in lowered
    assert "/my_subscription" in out.message_text
    assert "/get_access" in out.message_text
    assert "paid" not in lowered


def test_storefront_success_active_copy_safe() -> None:
    plan = _plan(category=OutboundPlanCategory.SUCCESS, message_key=OutboundMessageKey.STORE_SUCCESS_ACTIVE.value)
    out = render_telegram_outbound_plan(plan)
    lowered = out.message_text.lower()
    assert "подписк" in lowered and "активн" in lowered
    assert "/my_subscription" in out.message_text
    assert "/get_access" in out.message_text
    assert "payment payload" not in lowered
    _assert_no_dsn_or_secretish(out.message_text)


def test_storefront_renew_uses_renewal_then_checkout_fallback(monkeypatch) -> None:
    plan = _plan(category=OutboundPlanCategory.SUCCESS, message_key=OutboundMessageKey.STORE_RENEW.value)
    monkeypatch.setenv("TELEGRAM_STOREFRONT_CHECKOUT_URL", "https://example.com/checkout")
    monkeypatch.setenv("TELEGRAM_CHECKOUT_REFERENCE_SECRET", "Checkout_Secret_1234567890_ABC")
    monkeypatch.delenv("TELEGRAM_STOREFRONT_RENEWAL_URL", raising=False)
    out_checkout = render_telegram_outbound_plan(plan, telegram_user_id=12345)
    assert "https://example.com/checkout" in out_checkout.message_text
    assert "client_reference_id=" in out_checkout.message_text
    assert "client_reference_proof=" in out_checkout.message_text
    monkeypatch.setenv("TELEGRAM_STOREFRONT_RENEWAL_URL", "https://example.com/renew")
    out_renew = render_telegram_outbound_plan(plan, telegram_user_id=12345)
    assert "https://example.com/renew" in out_renew.message_text
    assert "client_reference_id=" in out_renew.message_text
    assert "client_reference_proof=" in out_renew.message_text


def test_support_menu_renders_faq_and_actions_keyboard() -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.SUPPORT_MENU.value,
        keyboard_marker=OutboundKeyboardMarker.SUPPORT_MENU.value,
    )
    out = render_telegram_outbound_plan(plan)
    assert "Помощь и поддержка" in out.message_text
    assert "Используйте /support_contact, чтобы связаться с нами." in out.message_text
    assert out.reply_markup is not None
    assert out.reply_markup["keyboard"] == [["📞 Контакты"]]


def test_support_contact_fallback_and_configured(monkeypatch) -> None:
    plan = _plan(
        category=OutboundPlanCategory.SUCCESS,
        message_key=OutboundMessageKey.SUPPORT_CONTACT.value,
        keyboard_marker=OutboundKeyboardMarker.SUPPORT_CONTACT.value,
    )
    monkeypatch.delenv("TELEGRAM_STOREFRONT_SUPPORT_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_STOREFRONT_SUPPORT_HANDLE", raising=False)
    out_fallback = render_telegram_outbound_plan(plan)
    assert "Поддержка временно недоступна. Пожалуйста, попробуйте позже." in out_fallback.message_text
    assert out_fallback.reply_markup is not None
    assert out_fallback.reply_markup["keyboard"] == [["📊 Тарифы", "💳 Купить"]]

    monkeypatch.setenv("TELEGRAM_STOREFRONT_SUPPORT_URL", "https://example.com/support")
    monkeypatch.setenv("TELEGRAM_STOREFRONT_SUPPORT_HANDLE", "@vpn_support")
    out_both = render_telegram_outbound_plan(plan)
    assert "https://example.com/support" in out_both.message_text
    assert "@vpn_support" in out_both.message_text

    monkeypatch.delenv("TELEGRAM_STOREFRONT_SUPPORT_URL", raising=False)
    out_handle_only = render_telegram_outbound_plan(plan)
    assert "@vpn_support" in out_handle_only.message_text
    assert "https://" not in out_handle_only.message_text

    monkeypatch.delenv("TELEGRAM_STOREFRONT_SUPPORT_HANDLE", raising=False)
    monkeypatch.setenv("TELEGRAM_STOREFRONT_SUPPORT_URL", "https://example.com/help")
    out_url_only = render_telegram_outbound_plan(plan)
    assert "https://example.com/help" in out_url_only.message_text
