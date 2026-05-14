"""In-memory tests for slice-1 bot transport service (raw update → adapter → dispatcher)."""

from __future__ import annotations

import inspect

from app.application.bootstrap import build_slice1_composition
from app.bot_transport.presentation import (
    TransportBootstrapCode,
    TransportErrorCode,
    TransportHelpCode,
    TransportNextActionHint,
    TransportResponseCategory,
    TransportSafeResponse,
    TransportStatusCode,
)
from app.bot_transport.service import Slice1TelegramService, handle_slice1_telegram_update
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


def test_service_raw_help_read_only() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/help"))
        r = await handle_slice1_telegram_update(raw, c, correlation_id=cid)
        assert r.category is TransportResponseCategory.SUCCESS
        assert r.code == TransportHelpCode.SLICE1_HELP.value
        assert r.replay_suppresses_outbound is False
        assert r.uc01_idempotency_key is None
        assert r.correlation_id == cid
        assert len(await c.audit.recorded_events()) == 0

    _run(main())


def test_service_raw_private_start_bootstrap_success() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/start"))
        r = await handle_slice1_telegram_update(raw, c, correlation_id=cid)
        assert r.category is TransportResponseCategory.SUCCESS
        assert r.code == TransportBootstrapCode.IDENTITY_READY.value
        assert r.correlation_id == cid

    _run(main())


def test_service_duplicate_raw_start_same_success_no_extra_audit() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(update_id=5, message=_base_message(user_id=42, text="/start"))
        r1 = await handle_slice1_telegram_update(raw, c, correlation_id=cid)
        r2 = await handle_slice1_telegram_update(raw, c, correlation_id=cid)
        assert r1.category is r2.category is TransportResponseCategory.SUCCESS
        assert r1.code == r2.code == TransportBootstrapCode.IDENTITY_READY.value
        assert len(await c.audit.recorded_events()) == 1

    _run(main())


def test_service_raw_status_unknown_user_onboarding_guidance() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(update_id=99, message=_base_message(user_id=999, text="/status"))
        r = await handle_slice1_telegram_update(raw, c, correlation_id=cid)
        assert r.category is TransportResponseCategory.GUIDANCE
        assert r.code == TransportStatusCode.NEEDS_ONBOARDING.value
        assert r.next_action_hint == TransportNextActionHint.COMPLETE_BOOTSTRAP.value
        assert r.correlation_id == cid

    _run(main())


def test_service_raw_status_after_bootstrap_no_snapshot_fail_closed() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        uid = 77
        await handle_slice1_telegram_update(
            _update(update_id=2, message=_base_message(user_id=uid, text="/start")),
            c,
            correlation_id=cid,
        )
        r = await handle_slice1_telegram_update(
            _update(message=_base_message(user_id=uid, text="/status")),
            c,
            correlation_id=cid,
        )
        assert r.category is TransportResponseCategory.SUCCESS
        assert r.code == TransportStatusCode.INACTIVE_OR_NOT_ELIGIBLE.value
        assert r.correlation_id == cid

    _run(main())


def test_service_callback_query_accepted() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(
            callback_query={"id": "q", "from": {"id": 1}, "data": "x"},
        )
        r = await handle_slice1_telegram_update(raw, c, correlation_id=cid)
        assert r.category is TransportResponseCategory.SUCCESS
        assert r.code == "x"
        assert r.correlation_id == cid

    _run(main())


def test_service_missing_message_rejected_safe() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        r = await handle_slice1_telegram_update({"update_id": 1}, c, correlation_id=cid)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value
        assert r.correlation_id == cid

    _run(main())


def test_service_missing_message_text_rejected_safe() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        m = _base_message(text="/x")
        del m["text"]
        r = await handle_slice1_telegram_update(_update(message=m), c, correlation_id=cid)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value
        assert r.correlation_id == cid

    _run(main())


def test_service_missing_user_rejected_safe() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        m = _base_message(text="/start")
        m["from"] = {"no_id": True}
        r = await handle_slice1_telegram_update(_update(message=m), c, correlation_id=cid)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value
        assert r.correlation_id == cid

    _run(main())


def test_service_correlation_id_preserved_when_provided() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        ok = await handle_slice1_telegram_update(
            _update(message=_base_message(text="/start")),
            c,
            correlation_id=cid,
        )
        assert ok.correlation_id == cid
        bad = await handle_slice1_telegram_update(
            _update(message=_base_message(text="/nope")),
            c,
            correlation_id=cid,
        )
        assert bad.correlation_id == cid

    _run(main())


def test_service_valid_generated_correlation_id_when_not_provided() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        raw = _update(message=_base_message(text="/start"))
        r = await handle_slice1_telegram_update(raw, c)
        assert is_valid_correlation_id(r.correlation_id)

    _run(main())


def test_service_adapter_rejects_do_not_leak_reason_to_transport() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raws = [
            _update(message=_base_message(text="hello")),
            _update(message=_base_message(text="/start", chat_type="group")),
        ]
        for raw in raws:
            r = await handle_slice1_telegram_update(raw, c, correlation_id=cid)
            assert isinstance(r, TransportSafeResponse)
            assert r.category is TransportResponseCategory.ERROR
            assert r.code == TransportErrorCode.INVALID_INPUT.value
            assert r.next_action_hint is None
            assert getattr(r, "reason", None) is None

    _run(main())


def test_service_module_excludes_billing_issuance_admin_concepts() -> None:
    import app.bot_transport.service as svc

    src = inspect.getsource(svc)
    lower = src.lower()
    assert "billing" not in lower
    assert "issuance" not in lower
    assert "admin" not in lower


def test_slice1_telegram_service_delegates() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        raw = _update(message=_base_message(text="/start"))
        svc = Slice1TelegramService()
        r = await svc.handle_telegram_update(raw, c, correlation_id=cid)
        assert r.category is TransportResponseCategory.SUCCESS
        assert r.correlation_id == cid

    _run(main())
