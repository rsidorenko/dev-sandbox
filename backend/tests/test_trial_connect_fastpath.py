"""Короткий Telegram-путь от активации триала до первого подключения."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.application.bootstrap import build_slice1_composition
from app.bot_transport.runtime_facade import handle_slice1_telegram_update_to_rendered_message
from app.bot_transport.storefront_ui import (
    CB_ANDROID_NO,
    CB_ANDROID_YES,
    CB_CONNECT_ANDROID,
    CB_CONNECT_DEVICE,
    CB_CONNECT_FAST_ANDROID,
    CB_CONNECT_FAST_IOS,
    CB_CONNECT_FAST_MAC,
    CB_CONNECT_FAST_WIN,
    CB_CONNECT_IOS,
    CB_CONNECT_MAC,
    CB_CONNECT_TV,
    CB_CONNECT_WIN,
    CB_IOS_NO,
    CB_IOS_YES,
    CB_MAC_NO,
    CB_MAC_YES,
    CB_MAIN_MENU,
    CB_TRIAL,
    CB_WIN_NO,
    CB_WIN_YES,
    connect_device_keyboard,
    no_subscription_keyboard,
    text_no_subscription,
)
from app.issuance.vless_provider import (
    StubVlessProvider,
    VlessProviderOutcome,
    VlessProviderResult,
    VlessUserConfig,
)
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run

_SUBSCRIPTION_URL = "https://bravada-connect.ru/sub/test-fastpath-token"


class _FastpathProvider(StubVlessProvider):
    def _build_config(self, internal_user_id: str) -> VlessProviderResult:
        return VlessProviderResult(
            outcome=VlessProviderOutcome.SUCCESS,
            config=VlessUserConfig(
                user_uuid=f"{internal_user_id}-uuid",
                subscription_url=_SUBSCRIPTION_URL,
                servers=(),
            ),
        )


def _message(text: str, *, user_id: int = 42) -> dict[str, object]:
    return {
        "message_id": 1,
        "from": {"id": user_id, "is_bot": False, "first_name": "U"},
        "chat": {"id": user_id, "type": "private"},
        "text": text,
    }


def _start_update(*, user_id: int = 42) -> dict[str, object]:
    return {"update_id": 1, "message": _message("/start", user_id=user_id)}


def _callback_update(callback_data: str, *, update_id: int, user_id: int = 42) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": None,
        "callback_query": {
            "id": f"fastpath-{update_id}",
            "from": {"id": user_id},
            "data": callback_data,
        },
    }


def _buttons(keyboard: dict[str, object] | None) -> list[dict[str, object]]:
    if keyboard is None:
        return []
    rows = keyboard.get("inline_keyboard")
    assert isinstance(rows, list)
    return [button for row in rows for button in row]


def test_device_selector_uses_fastpath_and_keeps_tv_details() -> None:
    callbacks = {button.get("callback_data") for button in _buttons(connect_device_keyboard())}
    assert {
        CB_CONNECT_FAST_IOS,
        CB_CONNECT_FAST_ANDROID,
        CB_CONNECT_FAST_MAC,
        CB_CONNECT_FAST_WIN,
        CB_CONNECT_TV,
        CB_MAIN_MENU,
    } <= callbacks
    assert not {CB_CONNECT_IOS, CB_CONNECT_ANDROID, CB_CONNECT_MAC, CB_CONNECT_WIN} & callbacks


def test_trial_activation_immediately_offers_platforms() -> None:
    async def main() -> None:
        composition = build_slice1_composition(vless_provider=_FastpathProvider())
        await handle_slice1_telegram_update_to_rendered_message(
            _start_update(), composition, correlation_id=new_correlation_id()
        )
        package = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(CB_TRIAL, update_id=2),
            composition,
            correlation_id=new_correlation_id(),
        )

        assert "Пробный период активирован" in package.message_text
        assert "Выберите устройство" in package.message_text
        callbacks = {button.get("callback_data") for button in _buttons(package.reply_markup)}
        assert {CB_CONNECT_FAST_IOS, CB_CONNECT_FAST_ANDROID, CB_CONNECT_FAST_MAC, CB_CONNECT_FAST_WIN} <= callbacks
        assert CB_MAIN_MENU in callbacks
        assert CB_CONNECT_DEVICE not in callbacks

    _run(main())


@pytest.mark.parametrize(
    ("fast_callback", "details_callback", "success_callback", "problem_callback", "app_name"),
    [
        (CB_CONNECT_FAST_IOS, CB_CONNECT_IOS, CB_IOS_YES, CB_IOS_NO, "Happ"),
        (CB_CONNECT_FAST_ANDROID, CB_CONNECT_ANDROID, CB_ANDROID_YES, CB_ANDROID_NO, "Happ"),
        (CB_CONNECT_FAST_MAC, CB_CONNECT_MAC, CB_MAC_YES, CB_MAC_NO, "Happ"),
        (CB_CONNECT_FAST_WIN, CB_CONNECT_WIN, CB_WIN_YES, CB_WIN_NO, "Karing"),
    ],
)
def test_fastpath_is_one_screen_with_copy_result_and_details(
    fast_callback: str,
    details_callback: str,
    success_callback: str,
    problem_callback: str,
    app_name: str,
) -> None:
    async def main() -> None:
        composition = build_slice1_composition(vless_provider=_FastpathProvider())
        await handle_slice1_telegram_update_to_rendered_message(
            _start_update(), composition, correlation_id=new_correlation_id()
        )
        await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(CB_TRIAL, update_id=2),
            composition,
            correlation_id=new_correlation_id(),
        )
        package = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(fast_callback, update_id=3),
            composition,
            correlation_id=new_correlation_id(),
        )

        assert "Подключение за минуту" in package.message_text
        assert app_name in package.message_text
        assert _SUBSCRIPTION_URL in package.message_text
        buttons = _buttons(package.reply_markup)
        assert any(button.get("copy_text") == {"text": _SUBSCRIPTION_URL} for button in buttons)
        callbacks = {button.get("callback_data") for button in buttons}
        assert {details_callback, success_callback, problem_callback, CB_CONNECT_DEVICE} <= callbacks
        assert all(_SUBSCRIPTION_URL not in str(button.get("callback_data", "")) for button in buttons)
        if fast_callback == CB_CONNECT_FAST_WIN:
            assert any(str(button.get("url", "")).endswith("?open=karing") for button in buttons)

    _run(main())


def test_fastpath_keyless_user_gets_safe_existing_prompt() -> None:
    async def main() -> None:
        composition = build_slice1_composition(vless_provider=_FastpathProvider())
        await handle_slice1_telegram_update_to_rendered_message(
            _start_update(), composition, correlation_id=new_correlation_id()
        )
        package = await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(CB_CONNECT_FAST_IOS, update_id=2),
            composition,
            correlation_id=new_correlation_id(),
        )

        assert package.message_text == text_no_subscription()
        assert package.reply_markup == no_subscription_keyboard()
        assert _SUBSCRIPTION_URL not in package.message_text

    _run(main())


def test_direct_trial_fastpath_records_setup_and_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.bot_transport.runtime_facade as runtime_facade

    record = AsyncMock(return_value=True)
    monkeypatch.setattr(runtime_facade, "record_interaction", record)

    async def main() -> None:
        composition = build_slice1_composition(vless_provider=_FastpathProvider())
        await handle_slice1_telegram_update_to_rendered_message(
            _start_update(), composition, correlation_id=new_correlation_id()
        )
        await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(CB_TRIAL, update_id=2),
            composition,
            correlation_id=new_correlation_id(),
        )
        await handle_slice1_telegram_update_to_rendered_message(
            _callback_update(CB_CONNECT_FAST_IOS, update_id=3),
            composition,
            correlation_id=new_correlation_id(),
        )

    _run(main())
    events = [(call.kwargs["event_name"], call.kwargs["platform"]) for call in record.await_args_list]
    assert events == [("setup_started", ""), ("platform_selected", "ios")]
