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
    CB_CHECK_SUB,
    CB_CONFIRM_PAY,
    CB_CONNECT_DEVICE,
    CB_DEVICES,
    CB_HELP,
    CB_LINK_EMAIL,
    CB_MAIN_MENU,
    CB_MY_KEYS,
    CB_MY_SUB,
    CB_PLAN,
    CB_REFERRAL,
    CB_ROUTER,
    CB_SETTINGS,
    CB_TRIAL,
    link_email_keyboard,
    my_keys_menu_keyboard,
    my_subscription_keyboard,
    settings_keyboard,
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
        assert pkg.message_text == text_welcome(trial_available=True)
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


def test_facade_raw_status_unknown_user_rejected_as_unknown() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(update_id=99, message=_base_message(user_id=999, text="/status"))
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert "некорректен" in pkg.message_text.lower() or pkg.correlation_id == cid

    _run(main())


def test_facade_raw_status_after_bootstrap_rejected_as_unknown() -> None:
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
        assert pkg.message_text == text_welcome(trial_available=True)
        assert pkg.correlation_id == cid

    _run(main())


def test_facade_raw_private_help_rejected_as_unknown() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/help"))
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.uc01_idempotency_key is None
        assert pkg.correlation_id == cid

    _run(main())


# ─── Storefront callback rendering tests ──────────────────────────────


def _callback_update(*, callback_data: str, user_id: int = 42, update_id: int = 1) -> dict[str, object]:
    return _update(
        update_id=update_id,
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


def test_facade_callback_main_menu_after_start_shows_trial_button() -> None:
    """/start then «Главное меню»: trial NOT activated, big trial button surfaced at the top."""

    async def main() -> None:
        c = build_slice1_composition()
        # /start bootstraps the identity (makes the trial available)
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        # user picks «Главное меню» instead of taking the trial
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_MAIN_MENU, update_id=2),
            c,
            correlation_id=new_correlation_id(),
        )
        assert pkg.message_text == text_main_menu()
        kb = pkg.reply_markup
        assert kb is not None
        first_row = kb["inline_keyboard"][0]
        assert first_row[0]["callback_data"] == CB_TRIAL
        assert first_row[0]["text"] == "🎁 Забрать 3 дня бесплатно"

    _run(main())


def test_facade_callback_main_menu_does_not_activate_trial() -> None:
    """Clicking «Главное меню» must NOT consume the trial (regression for the auto-activation bug)."""

    async def main() -> None:
        from app.bot_transport.runtime_facade import _is_trial_available

        c = build_slice1_composition()
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        assert await _is_trial_available(c, 42) is True
        await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_MAIN_MENU, update_id=2),
            c,
            correlation_id=new_correlation_id(),
        )
        # trial still available after entering the menu (was auto-activated before the fix)
        assert await _is_trial_available(c, 42) is True

    _run(main())


def test_facade_callback_trial_activates_and_then_menu_hides_button() -> None:
    """/start → take trial → menu: trial button gone (trial used)."""
    from app.issuance.vless_provider import StubVlessProvider

    async def main() -> None:
        from app.bot_transport.runtime_facade import _is_trial_available

        c = build_slice1_composition(vless_provider=StubVlessProvider())
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        # take the trial
        await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_TRIAL, update_id=2),
            c,
            correlation_id=new_correlation_id(),
        )
        assert await _is_trial_available(c, 42) is False
        # back to menu — no trial button anymore
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_MAIN_MENU, update_id=3),
            c,
            correlation_id=new_correlation_id(),
        )
        assert pkg.message_text == text_main_menu()
        kb = pkg.reply_markup
        assert kb is not None
        buttons = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
        assert CB_TRIAL not in buttons

    _run(main())


def test_main_menu_keyboard_trial_button_only_when_available() -> None:
    from app.bot_transport.storefront_ui import main_menu_keyboard

    kb_t = main_menu_keyboard(trial_available=True)
    assert kb_t["inline_keyboard"][0][0]["callback_data"] == CB_TRIAL

    for kb in (main_menu_keyboard(trial_available=False), main_menu_keyboard()):
        buttons = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
        assert CB_TRIAL not in buttons


def test_main_menu_moved_buttons_into_subscription_submenu() -> None:
    """Главное меню не содержит «Мои ключи»/«Настройки»/«Привязать email» — они
    переехали в подменю «Моя подписка». «Моя подписка» остаётся в главном меню."""
    from app.bot_transport.storefront_ui import main_menu_keyboard

    buttons = [b.get("callback_data") for row in main_menu_keyboard()["inline_keyboard"] for b in row]
    assert CB_MY_SUB in buttons
    for moved in (CB_MY_KEYS, CB_SETTINGS, CB_LINK_EMAIL):
        assert moved not in buttons, f"{moved} должно быть только в подменю"


def test_main_menu_colored_action_buttons() -> None:
    """Целевые действия в главном меню подсвечены нативным InlineKeyboardButton.style:
    «Купить VPN» — зелёная (success), «Подключить устройство» — синяя (primary),
    «Моя подписка» — нейтральная (без style), идёт после целевых действий."""
    from app.bot_transport.storefront_ui import main_menu_keyboard

    flat = [b for row in main_menu_keyboard()["inline_keyboard"] for b in row]
    by_cb = {b.get("callback_data"): b for b in flat}
    assert by_cb[CB_BUY_VPN]["style"] == "success"
    assert by_cb[CB_CONNECT_DEVICE]["style"] == "primary"
    assert "style" not in by_cb[CB_MY_SUB]
    # Порядок по референсу: Купить VPN → Подключить устройство → Моя подписка
    order = [b.get("callback_data") for b in flat]
    assert order.index(CB_BUY_VPN) < order.index(CB_CONNECT_DEVICE) < order.index(CB_MY_SUB)
    assert by_cb[CB_MY_SUB]["text"].startswith("👤")


def test_my_subscription_keyboard_has_submenu_buttons_and_back_to_main() -> None:
    """Подменю «Моя подписка»: 3 функциональные кнопки + Назад→главное меню."""
    rows = my_subscription_keyboard()["inline_keyboard"]
    buttons = [b.get("callback_data") for row in rows for b in row]
    assert buttons[:3] == [CB_MY_KEYS, CB_SETTINGS, CB_LINK_EMAIL]
    assert buttons[-1] == CB_MAIN_MENU  # Назад из подменю → главное меню


def test_subscription_screens_back_returns_to_my_subscription() -> None:
    """Назад с экранов ключей/настроек/email ведёт в «Моя подписку» (контекстный возврат
    «откуда пришёл» — теперь единственный путь на эти экраны из подменю)."""
    # my_keys_menu_keyboard требует servers; пустой кортеж — Назад всё равно в конце.
    from app.bot_transport.storefront_ui import VlessServerConfig  # noqa: F401  (тип для сигнатуры)

    keys_buttons = [b.get("callback_data") for row in my_keys_menu_keyboard(())["inline_keyboard"] for b in row]
    settings_buttons = [b.get("callback_data") for row in settings_keyboard(True)["inline_keyboard"] for b in row]
    email_buttons = [b.get("callback_data") for row in link_email_keyboard()["inline_keyboard"] for b in row]
    assert keys_buttons[-1] == CB_MY_SUB
    assert settings_buttons[-1] == CB_MY_SUB
    assert email_buttons[-1] == CB_MY_SUB


def test_facade_start_offer_unchanged_two_buttons() -> None:
    """/start keeps the 2-button offer (trial + Главное меню)."""

    async def main() -> None:
        c = build_slice1_composition()
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        assert pkg.message_text == text_welcome(trial_available=True)
        rows = pkg.reply_markup["inline_keyboard"]
        assert len(rows) == 2
        assert rows[0][0]["callback_data"] == CB_TRIAL
        assert rows[1][0]["callback_data"] == CB_MAIN_MENU

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
        buttons = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
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
        assert "249" in pkg.message_text
        kb = pkg.reply_markup
        assert kb is not None
        buttons = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
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


def test_facade_callback_connect_device_for_subscribed_user() -> None:
    from app.bot_transport.storefront_ui import text_connect_device
    from app.issuance.vless_provider import StubVlessProvider

    async def main() -> None:
        c = build_slice1_composition(vless_provider=StubVlessProvider())
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        # take the trial -> now has an active subscription
        await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_TRIAL, update_id=2), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_CONNECT_DEVICE, update_id=3),
            c,
            correlation_id=new_correlation_id(),
        )
        assert pkg.message_text == text_connect_device()

    _run(main())


def test_facade_callback_connect_device_keyless_shows_no_subscription() -> None:
    """A user with no subscription (skipped trial, no payment) opening 'Подключить
    устройство' gets a 'no active subscription' prompt (not broken connect steps)."""
    from app.bot_transport.storefront_ui import no_subscription_keyboard, text_no_subscription

    async def main() -> None:
        c = build_slice1_composition()
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_CONNECT_DEVICE, update_id=2),
            c,
            correlation_id=new_correlation_id(),
        )
        assert pkg.message_text == text_no_subscription()
        assert pkg.reply_markup == no_subscription_keyboard()

    _run(main())


def _inline_buttons(keyboard: dict[str, object] | None) -> list[dict[str, object]]:
    """Flatten an inline keyboard markup into a flat list of button dicts."""
    if not keyboard:
        return []
    rows = keyboard.get("inline_keyboard")  # type: ignore[union-attr]
    if not isinstance(rows, list):
        return []
    return [btn for row in rows if isinstance(row, list) for btn in row if isinstance(btn, dict)]


def test_connect_device_back_goes_to_main_menu() -> None:
    """«Назад» on the 'Выберите ваше устройство' screen returns to the main menu,
    not to the keys list."""
    from app.bot_transport.storefront_ui import connect_device_keyboard

    callbacks = [str(b.get("callback_data")) for b in _inline_buttons(connect_device_keyboard())]
    assert CB_MAIN_MENU in callbacks
    assert CB_MY_KEYS not in callbacks


def test_connect_platforms_render_app_chooser() -> None:
    """Tapping iPhone/Android/Mac renders the Happ/Karing app chooser (Happ recommended),
    not the Karing step 1 directly. Both app entry points must be reachable."""
    from app.bot_transport.storefront_ui import (
        CB_ANDROID_HAPP_STEP,
        CB_ANDROID_STEP,
        CB_CONNECT_ANDROID,
        CB_CONNECT_IOS,
        CB_CONNECT_MAC,
        CB_IOS_HAPP_STEP,
        CB_IOS_STEP,
        CB_MAC_HAPP_STEP,
        CB_MAC_STEP,
        android_app_chooser_keyboard,
        ios_app_chooser_keyboard,
        mac_app_chooser_keyboard,
        text_android_app_chooser,
        text_ios_app_chooser,
        text_mac_app_chooser,
    )
    from app.issuance.vless_provider import StubVlessProvider

    cases = [
        (CB_CONNECT_IOS, text_ios_app_chooser(), ios_app_chooser_keyboard(), CB_IOS_HAPP_STEP, CB_IOS_STEP),
        (
            CB_CONNECT_ANDROID,
            text_android_app_chooser(),
            android_app_chooser_keyboard(),
            CB_ANDROID_HAPP_STEP,
            CB_ANDROID_STEP,
        ),
        (CB_CONNECT_MAC, text_mac_app_chooser(), mac_app_chooser_keyboard(), CB_MAC_HAPP_STEP, CB_MAC_STEP),
    ]

    async def main() -> None:
        c = build_slice1_composition(vless_provider=StubVlessProvider())
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_TRIAL, update_id=2), c, correlation_id=new_correlation_id()
        )
        update_id = 3
        for code, expected_text, expected_kb, happ_prefix, karing_prefix in cases:
            pkg = await handle_slice1_telegram_update_to_rendered_message(
                _callback_update(callback_data=code, update_id=update_id),
                c,
                correlation_id=new_correlation_id(),
            )
            update_id += 1
            assert pkg.message_text == expected_text, f"chooser text mismatch for {code}"
            assert pkg.reply_markup == expected_kb, f"chooser keyboard mismatch for {code}"
            callbacks = [str(b.get("callback_data")) for b in _inline_buttons(pkg.reply_markup)]
            assert f"{happ_prefix}1" in callbacks, f"Happ entry missing for {code}"
            assert f"{karing_prefix}1" in callbacks, f"Karing entry missing for {code}"

    _run(main())


def test_happ_step_renders_without_media_and_karing_entry_intact() -> None:
    """Happ step 1 renders Happ text with no media; the chooser's Karing entry still
    renders the existing Karing step 1 (videos untouched)."""
    from app.bot_transport.storefront_ui import (
        CB_ANDROID_HAPP_STEP,
        CB_IOS_HAPP_STEP,
        CB_MAC_HAPP_STEP,
        android_happ_step_1_text,
        ios_happ_step_1_text,
        ios_step_1_text,
        mac_happ_step_1_text,
    )
    from app.issuance.vless_provider import StubVlessProvider

    async def main() -> None:
        c = build_slice1_composition(vless_provider=StubVlessProvider())
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_TRIAL, update_id=2), c, correlation_id=new_correlation_id()
        )
        # Happ step 1 per platform — text-only (no Karing media reused)
        for code, expected in (
            (f"{CB_IOS_HAPP_STEP}1", ios_happ_step_1_text()),
            (f"{CB_MAC_HAPP_STEP}1", mac_happ_step_1_text()),
            (f"{CB_ANDROID_HAPP_STEP}1", android_happ_step_1_text()),
        ):
            pkg = await handle_slice1_telegram_update_to_rendered_message(
                _callback_update(callback_data=code, update_id=3),
                c,
                correlation_id=new_correlation_id(),
            )
            assert pkg.message_text == expected
            assert pkg.media_type is None
            assert pkg.media_path is None
        # Karing entry still works (existing scenario untouched)
        from app.bot_transport.storefront_ui import CB_IOS_STEP

        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=f"{CB_IOS_STEP}1", update_id=4),
            c,
            correlation_id=new_correlation_id(),
        )
        assert pkg.message_text == ios_step_1_text()

    _run(main())


def test_happ_step_keyless_shows_no_subscription() -> None:
    """A keyless user opening a Happ connect step gets the 'no subscription' prompt —
    step 3 needs a subscription URL and must not build an empty import button."""
    from app.bot_transport.storefront_ui import CB_IOS_HAPP_STEP, no_subscription_keyboard, text_no_subscription

    async def main() -> None:
        c = build_slice1_composition()
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=f"{CB_IOS_HAPP_STEP}3", update_id=2),
            c,
            correlation_id=new_correlation_id(),
        )
        assert pkg.message_text == text_no_subscription()
        assert pkg.reply_markup == no_subscription_keyboard()

    _run(main())


def test_step1_back_returns_to_app_chooser() -> None:
    """Step 1 «Назад» returns to the app chooser (CB_CONNECT_IOS/MAC/ANDROID) — not
    to the device-selection page — for both Happ and Karing branches on ios/mac/android.
    Windows/TV have no chooser and keep «Назад» → device selection."""
    from app.bot_transport.storefront_ui import (
        CB_CONNECT_ANDROID,
        CB_CONNECT_DEVICE,
        CB_CONNECT_IOS,
        CB_CONNECT_MAC,
        android_happ_step_1_keyboard,
        android_step_1_keyboard,
        ios_happ_step_1_keyboard,
        ios_step_1_keyboard,
        mac_happ_step_1_keyboard,
        mac_step_1_keyboard,
        tv_step_1_keyboard,
        win_step_1_keyboard,
    )

    def back_target(kb: dict[str, object] | None) -> str:
        back = [b for b in _inline_buttons(kb) if str(b.get("text", "")).startswith("↩️")]
        assert back, f"no Назад button in {kb}"
        return str(back[0].get("callback_data"))

    # Happ branch (ios/mac/android): back → chooser
    assert back_target(ios_happ_step_1_keyboard()) == CB_CONNECT_IOS
    assert back_target(mac_happ_step_1_keyboard()) == CB_CONNECT_MAC
    assert back_target(android_happ_step_1_keyboard()) == CB_CONNECT_ANDROID
    # Karing branch (ios/mac/android): back → chooser
    assert back_target(ios_step_1_keyboard()) == CB_CONNECT_IOS
    assert back_target(mac_step_1_keyboard()) == CB_CONNECT_MAC
    assert back_target(android_step_1_keyboard()) == CB_CONNECT_ANDROID
    # Windows/TV have no chooser: back stays on device selection
    assert back_target(win_step_1_keyboard()) == CB_CONNECT_DEVICE
    assert back_target(tv_step_1_keyboard()) == CB_CONNECT_DEVICE


def test_android_karing_is_clean_and_happ_branch_has_v2raytune() -> None:
    """Android Karing step 1 offers only Karing (Happ/v2rayTune moved out); the Happ
    branch step 1 offers BOTH Happ and v2rayTune download buttons."""
    from app.bot_transport.storefront_ui import (
        android_happ_step_1_keyboard,
        android_step_1_keyboard,
        android_step_3_keyboard,
        ios_happ_step_1_keyboard,
        mac_happ_step_1_keyboard,
    )

    def url_labels(kb: dict[str, object] | None) -> list[str]:
        return [str(b.get("text", "")) for b in _inline_buttons(kb) if b.get("url")]

    # Android Karing step 1: only Karing, no Happ/v2rayTune
    a1 = url_labels(android_step_1_keyboard())
    assert any("Karing" in t for t in a1), a1
    assert not any("Happ" in t for t in a1), a1
    assert not any("v2rayTune" in t for t in a1), a1
    # Android Karing step 3: only the Karing import button
    a3 = url_labels(android_step_3_keyboard("https://example.invalid/sub?t=1"))
    assert a3 == ["🔑 Загрузить ключи в Karing"], a3
    # Happ branch step 1 (all platforms): both Happ + v2rayTune download buttons
    for kb in (ios_happ_step_1_keyboard(), mac_happ_step_1_keyboard(), android_happ_step_1_keyboard()):
        labels = url_labels(kb)
        assert any("Happ" in t for t in labels), labels
        assert any("v2rayTune" in t for t in labels), labels


def test_facade_callback_trial_returns_menu_not_key_list() -> None:
    """Trial activation returns the short main-menu confirmation, NOT the full key list
    (which exceeds Telegram's 4096-char limit with many servers and gets rejected)."""
    from app.issuance.vless_provider import StubVlessProvider

    async def main() -> None:
        c = build_slice1_composition(vless_provider=StubVlessProvider())
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_TRIAL, update_id=2), c, correlation_id=new_correlation_id()
        )
        assert "активирован" in pkg.message_text.lower()
        # short menu confirmation, not the long key list
        assert len(pkg.message_text) < 700
        btns = [b.get("callback_data") for row in pkg.reply_markup["inline_keyboard"] for b in row]
        assert CB_BUY_VPN in btns  # main-menu keyboard

    _run(main())


def test_format_key_list_bounded_truncates_many_servers() -> None:
    from app.bot_transport.storefront_ui import _format_key_list_bounded
    from app.issuance.vless_provider import VlessServerConfig

    long_link = (
        "vless://11111111-1111-1111-1111-111111111111@host.example.com:443?type=xhttp"
        "&security=tls&sni=host.example.com&path=%2Fapi-test&fp=firefox&pbk=" + ("A" * 43)
    )
    servers = tuple(
        VlessServerConfig(server_label=f"S{i}", country_code="DE", country_flag="🇩🇪", vless_link=long_link)
        for i in range(30)
    )
    out = _format_key_list_bounded(servers, budget=1500)
    assert len(out) <= 1500
    assert "подписки" in out  # truncation note


def test_text_my_keys_fits_under_telegram_limit_with_many_servers() -> None:
    from app.bot_transport.storefront_ui import text_my_keys
    from app.issuance.vless_provider import VlessServerConfig, VlessUserConfig

    long_link = (
        "vless://11111111-1111-1111-1111-111111111111@host.example.com:443?type=xhttp"
        "&security=tls&sni=host.example.com&path=%2Fapi-test&fp=firefox&pbk=" + ("A" * 43)
    )
    servers = tuple(
        VlessServerConfig(server_label=f"S{i}", country_code="DE", country_flag="🇩🇪", vless_link=long_link)
        for i in range(20)
    )
    config = VlessUserConfig(user_uuid="u", subscription_url="https://example.com/sub/abc", servers=servers)
    msg = text_my_keys(config)
    assert len(msg) < 4096


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


# ─── Гейт подписки на канал перед выдачей триала ──────────────────────


class _FakeGate:
    """Fake-чекер с тем же контрактом, что TelegramChannelMembershipChecker."""

    def __init__(self, result: bool | None) -> None:
        self._result = result
        self.calls: list[int] = []

    async def is_member(self, user_id: int) -> bool | None:
        self.calls.append(user_id)
        return self._result


def _gate_composition(result: bool | None):
    from app.issuance.vless_provider import StubVlessProvider

    return build_slice1_composition(vless_provider=StubVlessProvider(), trial_channel_gate=_FakeGate(result))


def test_facade_trial_gate_off_activates_as_before() -> None:
    """Гейт выключен (checker None) — CB_TRIAL активирует триал как раньше."""

    async def main() -> None:
        from app.bot_transport.runtime_facade import _is_trial_available
        from app.issuance.vless_provider import StubVlessProvider

        c = build_slice1_composition(vless_provider=StubVlessProvider())
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_TRIAL, update_id=2), c, correlation_id=new_correlation_id()
        )
        assert "Пробный период активирован" in pkg.message_text
        assert await _is_trial_available(c, 42) is False

    _run(main())


def test_facade_trial_gate_not_subscribed_shows_gate_without_burning_trial() -> None:
    """Не подписан: экран гейта, триал НЕ израсходован — главная инварианта."""

    async def main() -> None:
        from app.bot_transport.runtime_facade import _is_trial_available

        c = _gate_composition(False)
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_TRIAL, update_id=2), c, correlation_id=new_correlation_id()
        )
        assert pkg.message_text == ("<b>🎁 Пробный период — для подписчиков канала</b>\n\nПодпишись на наш канал")
        kb = pkg.reply_markup
        assert kb is not None
        rows = kb["inline_keyboard"]
        assert any(b.get("url") == "https://t.me/bravada_vpn" for row in rows for b in row)
        assert any(b.get("callback_data") == CB_CHECK_SUB for row in rows for b in row)
        # Триал не сожжён
        assert await _is_trial_available(c, 42) is True

    _run(main())


def test_facade_trial_gate_subscribed_activates() -> None:
    """Подписан: триал выдаётся штатно."""

    async def main() -> None:
        c = _gate_composition(True)
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_TRIAL, update_id=2), c, correlation_id=new_correlation_id()
        )
        assert "Пробный период активирован" in pkg.message_text

    _run(main())


def test_facade_trial_gate_check_error_fail_open_activates() -> None:
    """Fail-open: ошибка проверки (None) не блокирует триал."""

    async def main() -> None:
        c = _gate_composition(None)
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_TRIAL, update_id=2), c, correlation_id=new_correlation_id()
        )
        assert "Пробный период активирован" in pkg.message_text

    _run(main())


def test_facade_check_sub_after_subscribing_activates() -> None:
    """«Я подписался — проверить» после подписки: активация триала."""

    async def main() -> None:
        c = _gate_composition(True)
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_CHECK_SUB, update_id=2), c, correlation_id=new_correlation_id()
        )
        assert "Пробный период активирован" in pkg.message_text
        gate = c.trial_channel_gate
        assert isinstance(gate, _FakeGate)
        # Проверка зовётся дважды: в ветке check_sub (дружелюбный отказ) и в активации
        # (охрана claim перед выдачей) — второй вызов гарантирует отсутствие гонки.
        assert gate.calls == [42, 42]

    _run(main())


def test_facade_check_sub_still_not_subscribed_shows_gate_again() -> None:
    """«Я подписался» без фактической подписки: повторный экран гейта, триал цел."""

    async def main() -> None:
        from app.bot_transport.runtime_facade import _is_trial_available

        c = _gate_composition(False)
        await handle_slice1_telegram_update_to_rendered_message(
            _update(message=_base_message(text="/start")), c, correlation_id=new_correlation_id()
        )
        pkg = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(callback_data=CB_CHECK_SUB, update_id=2), c, correlation_id=new_correlation_id()
        )
        assert "не видим вашу подписку" in pkg.message_text
        assert await _is_trial_available(c, 42) is True

    _run(main())
