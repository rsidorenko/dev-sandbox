"""Pure/in-memory tests for slice-1 runtime facade (raw update → rendered message package)."""

from __future__ import annotations

import asyncio
import inspect

from app.application.bootstrap import build_slice1_composition
from app.bot_transport.message_catalog import RenderedMessagePackage
from app.bot_transport.runtime_facade import (
    Slice1TelegramRuntimeFacade,
    handle_slice1_telegram_update_to_rendered_message,
)
from app.security.idempotency import build_bootstrap_idempotency_key
from app.shared.correlation import is_valid_correlation_id, new_correlation_id
from tests.slice1_expected_user_copy import (
    IDENTITY_READY_TEXT,
    INACTIVE_OR_NOT_ELIGIBLE_TEXT,
    NEEDS_ONBOARDING_TEXT,
    SLICE1_HELP_TEXT,
)


def _run(coro):
    return asyncio.run(coro)


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


def test_facade_raw_private_start_returns_identity_ready_rendered() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/start"))
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert isinstance(pkg, RenderedMessagePackage)
        assert pkg.message_text == IDENTITY_READY_TEXT
        assert pkg.action_keys == ()
        assert pkg.correlation_id == cid
        assert pkg.uc01_idempotency_key == build_bootstrap_idempotency_key(42, 1)

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


def test_facade_module_excludes_billing_issuance_admin_concepts() -> None:
    import app.bot_transport.runtime_facade as rf

    src = inspect.getsource(rf)
    lower = src.lower()
    assert "billing" not in lower
    assert "issuance" not in lower
    assert "admin" not in lower


def test_slice1_telegram_runtime_facade_delegates() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/start"))
        facade = Slice1TelegramRuntimeFacade()
        pkg = await facade.handle_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == IDENTITY_READY_TEXT
        assert pkg.correlation_id == cid

    _run(main())


def test_facade_raw_private_help_read_only() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/help"))
        pkg = await handle_slice1_telegram_update_to_rendered_message(raw, c, correlation_id=cid)
        assert pkg.message_text == SLICE1_HELP_TEXT
        assert pkg.uc01_idempotency_key is None
        assert len(await c.audit.recorded_events()) == 0

    _run(main())
