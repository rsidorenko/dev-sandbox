"""Pure/in-memory tests for slice-1 runtime facade (raw update → rendered message package)."""

from __future__ import annotations

import inspect

from app.application.bootstrap import build_slice1_composition
from app.bot_transport.message_catalog import RenderedMessagePackage
from app.bot_transport.runtime_facade import (
    Slice1TelegramRuntimeFacade,
    handle_slice1_telegram_update_to_rendered_message,
)
from app.bot_transport.storefront_ui import (
    CB_BALANCE,
    CB_BUY_VPN,
    CB_CONFIRM_PAY,
    CB_DEVICES,
    CB_HELP,
    CB_MAIN_MENU,
    CB_MY_KEYS,
    CB_MY_SUB,
    CB_PLAN,
    CB_REFERRAL,
    CB_ROUTER,
    CB_SETTINGS,
    CB_SUB_URL,
    text_buy_vpn_intro,
    text_help,
    text_keys_not_available,
    text_main_menu,
    text_no_subscription,
    text_purchase_summary,
    text_router_soon,
    text_settings,
    text_welcome,
)
from app.security.idempotency import build_bootstrap_idempotency_key
from app.shared.correlation import is_valid_correlation_id, new_correlation_id
from app.shared.test_helpers import run_async as _run
from tests.slice1_expected_user_copy import (
    INACTIVE_OR_NOT_ELIGIBLE_TEXT,
    NEEDS_ONBOARDING_TEXT,
)


def _base_message(*, text: str, user_id: int = 42, chat_type: str = "private") -> dict[str, object]:
    return {
        "message_id": 1,
        "from": {"id": user_id, "is_bot": False, "first_name": "U"},
        "chat": {"id": user_id, "type": chat_type},
        "text": text,
    }


def _update(
    *,
    update_id: int = 1,
    message: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    u: dict[str, object] = {"update_id": update_id, "message": message}
    u.update(extra)
    return u


def test_facade_raw_private_start_returns_storefront_welcome() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/start"))
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert isinstance(pkg, RenderedMessagePackage)
        assert pkg.message_text == text_welcome()
        assert pkg.action_keys == ()
        assert pkg.correlation_id == cid
        assert pkg.uc01_idempotency_key == build_bootstrap_idempotency_key(42, 1)
        assert pkg.reply_markup is not None
        assert "inline_keyboard" in pkg.reply_markup

    _run(main())


def test_facade_duplicate_raw_start_replay_flag_second_call_one_audit() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(update_id=5, message=_base_message(user_id=42, text="/start"))
        p1 = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        p2 = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert p1.message_text == p2.message_text
        assert p1.replay_suppresses_outbound is False
        assert p2.replay_suppresses_outbound is True
        assert p1.correlation_id == p2.correlation_id == cid
        assert p1.uc01_idempotency_key == p2.uc01_idempotency_key == build_bootstrap_idempotency_key(42, 5)
        assert len(await c.audit.recorded_events()) == 1

    _run(main())


def test_facade_raw_status_unknown_user_onboarding_guidance_rendered() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(update_id=99, message=_base_message(user_id=999, text="/status"))
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == NEEDS_ONBOARDING_TEXT
        assert pkg.action_keys == ("complete_bootstrap",)
        assert pkg.correlation_id == cid
        assert pkg.uc01_idempotency_key is None

    _run(main())


def test_facade_raw_status_after_bootstrap_no_snapshot_fail_closed_rendered() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        uid = 77
        await handle_slice1_telegram_update_to_rendered_message(
            _update(update_id=2, message=_base_message(user_id=uid, text="/start")),
            c,
            correlation_id=cid,
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(user_id=uid, text="/status")),
            c,
            correlation_id=cid,
        )
        assert pkg.message_text == INACTIVE_OR_NOT_ELIGIBLE_TEXT
        assert pkg.correlation_id == cid

    _run(main())


def test_facade_callback_query_rendered_as_unknown_action() -> None:
    """Callback_query is now accepted; unknown action code maps to service_unavailable catalog copy."""

    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(
            callback_query={"id": "q", "from": {"id": 1}, "data": "x"},
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.correlation_id == cid

    _run(main())


def test_facade_invalid_inputs_safe_no_exception() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        cases = [
            {"update_id": 1},
            _update(message=_base_message(text="/nope")),
            _update(message=_base_message(text="/start", chat_type="group")),
        ]
        for raw in cases:
            pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
            assert isinstance(pkg, RenderedMessagePackage)
            assert pkg.correlation_id == cid

    _run(main())


def test_facade_correlation_id_preserved_when_provided() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        ok = await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")),
            c,
            correlation_id=cid,
        )
        assert ok.correlation_id == cid
        bad = await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/nope")),
            c,
            correlation_id=cid,
        )
        assert bad.correlation_id == cid

    _run(main())


def test_facade_generated_correlation_id_when_omitted() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        raw = _update(message=_base_message(text="/start"))
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c)
        assert is_valid_correlation_id(pkg.correlation_id)

    _run(main())


def test_facade_module_excludes_billing_admin_concepts() -> None:
    import app.bot_transport.runtime_facade as rf

    src = inspect.getsource(rf)
    lower = src.lower()
    assert "billing" not in lower
    assert "admin" not in lower


def test_slice1_telegram_runtime_facade_delegates() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/start"))
        facade = Slice1TelegramRuntimeFacade()
        pkg = await facade.handle_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == text_welcome()
        assert pkg.correlation_id == cid

    _run(main())


def test_facade_raw_private_help_storefront_rendered() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/help"))
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == text_help()
        assert pkg.uc01_idempotency_key is None
        assert len(await c.audit.recorded_events()) == 0
        assert pkg.reply_markup is not None
        assert "inline_keyboard" in pkg.reply_markup

    _run(main())


# ─── Storefront callback rendering tests ──────────────────────────────


def _callback_update(*, callback_data: str, user_id: int = 42) -> dict[str, object]:
    return _update(
        callback_query={"id": "cq1", "from": {"id": user_id}, "data": callback_data},
    )


def test_facade_callback_main_menu_renders_storefront() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=CB_MAIN_MENU)
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == text_main_menu()
        assert pkg.reply_markup is not None
        assert "inline_keyboard" in pkg.reply_markup
        assert pkg.correlation_id == cid

    _run(main())


def test_facade_callback_buy_vpn_renders_plans_list() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=CB_BUY_VPN)
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == text_buy_vpn_intro()
        kb = pkg.reply_markup
        assert kb is not None
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        assert any(b.startswith("plan:") for b in buttons)

    _run(main())


def test_facade_callback_help_renders_storefront_help() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=CB_HELP)
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == text_help()
        assert pkg.reply_markup is not None

    _run(main())


def test_facade_callback_plan_select_renders_device_select() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=f"{CB_PLAN}1m")
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert "1 мес" in pkg.message_text
        assert "300" in pkg.message_text
        kb = pkg.reply_markup
        assert kb is not None
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        assert any(b.startswith("devices:") for b in buttons)

    _run(main())


def test_facade_callback_devices_changes_count() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=f"{CB_DEVICES}1m:7")
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert "7" in pkg.message_text
        assert "Устройств: 7" in pkg.reply_markup["inline_keyboard"][0][1]["text"]

    _run(main())


def test_facade_callback_confirm_pay_renders_summary() -> None:
    async def main() -> None:
        from app.application.purchase_handler import build_purchase_summary

        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=f"{CB_CONFIRM_PAY}1m:5")
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        summary = build_purchase_summary(plan_id="1m", device_count=5)
        assert pkg.message_text == text_purchase_summary(summary)

    _run(main())


def test_facade_callback_my_keys_not_available() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=CB_MY_KEYS)
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == text_keys_not_available()

    _run(main())


def test_facade_callback_subscription_url_not_available() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=CB_SUB_URL)
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == text_keys_not_available()

    _run(main())


def test_facade_callback_my_sub_no_subscription() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=CB_MY_SUB, user_id=999)
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == text_no_subscription()

    _run(main())


def test_facade_callback_router_soon() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=CB_ROUTER)
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == text_router_soon()

    _run(main())


def test_facade_callback_settings_no_subscription() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=CB_SETTINGS, user_id=999)
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == text_settings(has_subscription=False)

    _run(main())


def test_facade_callback_referral_placeholder() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=CB_REFERRAL)
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert "Реферальная программа" in pkg.message_text

    _run(main())


def test_facade_callback_balance_placeholder() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _callback_update(callback_data=CB_BALANCE)
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert "баланс" in pkg.message_text.lower()

    _run(main())
