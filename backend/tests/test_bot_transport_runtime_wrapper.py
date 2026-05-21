"""In-memory tests for slice-1 pure runtime wrapper (update → runtime action, no SDK)."""

from __future__ import annotations

import inspect

from app.application.bootstrap import build_slice1_composition
from app.bot_transport.presentation import TransportResponseCategory
from app.bot_transport.runtime_wrapper import (
    Slice1TelegramRuntimeWrapper,
    TelegramRuntimeAction,
    TelegramRuntimeActionKind,
    extract_eligible_private_chat_id_from_telegram_like_update,
    handle_slice1_telegram_update_to_runtime_action,
)
from app.bot_transport.storefront_ui import text_help, text_welcome
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


def test_runtime_wrapper_private_start_send_message() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/start"))
        action = await handle_slice1_telegram_update_to_runtime_action(raw, c, correlation_id=cid)
        assert action.kind is TelegramRuntimeActionKind.SEND_MESSAGE
        assert action.chat_id == 42
        assert action.message_text == text_welcome(trial_available=True)
        assert action.action_keys == ()
        assert action.reply_markup is not None
        assert "inline_keyboard" in action.reply_markup
        assert action.correlation_id == cid

    _run(main())


def test_runtime_wrapper_duplicate_private_start_first_send_second_noop_one_audit() -> None:
    """Second identical raw /start (same update_id) is UC-01 replay: outbound suppressed (NOOP).

    Suppress-send does not address first send failing after commit without a delivery ledger.
    """

    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(update_id=5, message=_base_message(user_id=42, text="/start"))
        a1 = await handle_slice1_telegram_update_to_runtime_action(raw, c, correlation_id=cid)
        a2 = await handle_slice1_telegram_update_to_runtime_action(raw, c, correlation_id=cid)
        assert a1.kind is TelegramRuntimeActionKind.SEND_MESSAGE
        assert a2.kind is TelegramRuntimeActionKind.NOOP
        assert a1.message_text
        assert a2.message_text is None
        assert a1.correlation_id == a2.correlation_id == cid
        assert len(await c.audit.recorded_events()) == 1

    _run(main())


def test_runtime_wrapper_private_status_unknown_user_send_onboarding() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(update_id=99, message=_base_message(user_id=999, text="/status"))
        action = await handle_slice1_telegram_update_to_runtime_action(raw, c, correlation_id=cid)
        assert action.kind is TelegramRuntimeActionKind.SEND_MESSAGE
        assert action.chat_id == 999
        assert action.message_text == NEEDS_ONBOARDING_TEXT
        assert action.action_keys == ("complete_bootstrap",)
        assert action.correlation_id == cid

    _run(main())


def test_runtime_wrapper_private_status_after_bootstrap_no_snapshot_inactive_send() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        uid = 77
        await handle_slice1_telegram_update_to_runtime_action(
            _update(update_id=2, message=_base_message(user_id=uid, text="/start")),
            c,
            correlation_id=cid,
        )
        action = await handle_slice1_telegram_update_to_runtime_action(
            _update(message=_base_message(user_id=uid, text="/status")),
            c,
            correlation_id=cid,
        )
        assert action.kind is TelegramRuntimeActionKind.SEND_MESSAGE
        assert action.chat_id == uid
        assert action.message_text == INACTIVE_OR_NOT_ELIGIBLE_TEXT
        assert action.correlation_id == cid

    _run(main())


def test_runtime_wrapper_private_help_send_message_no_uc01_ledger() -> None:
    """read-only /help: one send, no UC-01 idempotency key, no delivery ledger handoff."""

    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(
            update_id=50,
            message=_base_message(user_id=33, text="/help"),
        )
        action = await handle_slice1_telegram_update_to_runtime_action(raw, c, correlation_id=cid)
        assert action.kind is TelegramRuntimeActionKind.SEND_MESSAGE
        assert action.message_text == text_help()
        assert action.action_keys == ()
        assert action.reply_markup is not None
        assert action.uc01_idempotency_key is None
        assert action.chat_id == 33
        assert len(await c.audit.recorded_events()) == 0

    _run(main())


def test_runtime_wrapper_private_unknown_slash_send_invalid_input() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/unknown"))
        action = await handle_slice1_telegram_update_to_runtime_action(raw, c, correlation_id=cid)
        assert action.kind is TelegramRuntimeActionKind.SEND_MESSAGE
        assert action.message_text == "❌ Ввод некорректен. Попробуйте снова."
        assert action.correlation_id == cid

    _run(main())


def test_runtime_wrapper_non_private_start_noop() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/start", chat_type="group"))
        action = await handle_slice1_telegram_update_to_runtime_action(raw, c, correlation_id=cid)
        assert action.kind is TelegramRuntimeActionKind.NOOP
        assert action.chat_id is None
        assert action.message_text is None
        assert action.action_keys == ()
        assert action.reply_markup is None
        assert is_valid_correlation_id(action.correlation_id)

    _run(main())


def test_runtime_wrapper_malformed_no_message_noop() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        action = await handle_slice1_telegram_update_to_runtime_action(
            {"update_id": 1},
            c,
            correlation_id=cid,
        )
        assert action.kind is TelegramRuntimeActionKind.NOOP
        assert action.chat_id is None
        assert action.message_text is None
        assert action.action_keys == ()
        assert action.reply_markup is None
        assert is_valid_correlation_id(action.correlation_id)

    _run(main())


def test_runtime_wrapper_invalid_correlation_override_action_still_valid() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        raw = _update(message=_base_message(text="/start"))
        action = await handle_slice1_telegram_update_to_runtime_action(
            raw,
            c,
            correlation_id="not-a-valid-correlation-id",
        )
        assert action.kind is TelegramRuntimeActionKind.SEND_MESSAGE
        assert is_valid_correlation_id(action.correlation_id)

    _run(main())


def test_runtime_action_shape_no_raw_payload_or_internal_enums() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/start"))
        action = await handle_slice1_telegram_update_to_runtime_action(raw, c, correlation_id=cid)
        assert isinstance(action, TelegramRuntimeAction)
        assert isinstance(action.kind, TelegramRuntimeActionKind)
        for name in (
            "chat_id",
            "message_text",
            "correlation_id",
            "action_keys",
            "reply_markup",
            "kind",
            "uc01_idempotency_key",
        ):
            assert hasattr(action, name)
        assert isinstance(action.reply_markup, dict) or action.reply_markup is None
        assert TransportResponseCategory not in type(action).__mro__

    _run(main())


def test_runtime_wrapper_module_excludes_billing_issuance_admin() -> None:
    import app.bot_transport.runtime_wrapper as rw

    src = inspect.getsource(rw)
    lower = src.lower()
    assert "billing" not in lower
    assert "issuance" not in lower
    assert "admin" not in lower


def test_slice1_telegram_runtime_wrapper_handle_and_dispatch() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/start"))
        w = Slice1TelegramRuntimeWrapper(c)
        a1 = await w.handle(raw, correlation_id=cid)
        a2 = await w.dispatch(raw, correlation_id=cid)
        assert a1.kind is TelegramRuntimeActionKind.SEND_MESSAGE
        assert a2.kind is TelegramRuntimeActionKind.NOOP
        assert a1.message_text
        assert a2.message_text is None

    _run(main())


def test_extract_eligible_chat_private_consistent_ids() -> None:
    raw = _update(message=_base_message(user_id=100, text="/start"))
    assert extract_eligible_private_chat_id_from_telegram_like_update(raw) == 100


def test_extract_eligible_chat_mismatch_from_and_chat_returns_none() -> None:
    m = _base_message(user_id=1, text="/start")
    m["chat"] = {"id": 2, "type": "private"}
    raw = _update(message=m)
    assert extract_eligible_private_chat_id_from_telegram_like_update(raw) is None
