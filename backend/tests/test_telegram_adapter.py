"""Pure tests: Telegram-shaped update → :class:`TransportIncomingEnvelope` (no SDK, no IO)."""

from __future__ import annotations

from dataclasses import fields

from app.bot_transport.normalized import (
    NormalizedSlice1Rejected,
    TransportIncomingEnvelope,
    parse_slice1_transport,
)
from app.bot_transport.telegram_adapter import (
    TelegramAdapterRejectReason,
    TelegramAdapterRejected,
    extract_slice1_envelope_from_telegram_update,
)
from app.shared.correlation import is_valid_correlation_id, new_correlation_id


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


def test_private_start_yields_valid_envelope() -> None:
    cid = new_correlation_id()
    raw = _update(message=_base_message(text="/start"))
    r = extract_slice1_envelope_from_telegram_update(raw, correlation_id=cid)
    assert isinstance(r, TransportIncomingEnvelope)
    assert r.telegram_user_id == 42
    assert r.telegram_update_id == 1
    assert r.correlation_id == cid
    assert r.normalized_command_text == "/start"
    parsed = parse_slice1_transport(r)
    assert not isinstance(parsed, NormalizedSlice1Rejected)


def test_private_status_yields_valid_envelope() -> None:
    cid = new_correlation_id()
    raw = _update(update_id=99, message=_base_message(text="/status"))
    r = extract_slice1_envelope_from_telegram_update(raw, correlation_id=cid)
    assert isinstance(r, TransportIncomingEnvelope)
    assert r.telegram_update_id == 99
    assert r.normalized_command_text == "/status"


def test_start_with_bot_suffix_extracts() -> None:
    cid = new_correlation_id()
    raw = _update(message=_base_message(text="/start@MyBot"))
    r = extract_slice1_envelope_from_telegram_update(raw, correlation_id=cid)
    assert isinstance(r, TransportIncomingEnvelope)
    assert r.normalized_command_text == "/start@MyBot"
    parsed = parse_slice1_transport(r)
    assert not isinstance(parsed, NormalizedSlice1Rejected)


def test_unknown_slash_command_passes_envelope_normalized_rejects() -> None:
    """Allowlist stays in normalized; adapter only bounds Telegram shape."""
    cid = new_correlation_id()
    raw = _update(message=_base_message(text="/unknown"))
    r = extract_slice1_envelope_from_telegram_update(raw, correlation_id=cid)
    assert isinstance(r, TransportIncomingEnvelope)
    parsed = parse_slice1_transport(r)
    assert isinstance(parsed, NormalizedSlice1Rejected)


def test_update_without_message_rejected() -> None:
    cid = new_correlation_id()
    r = extract_slice1_envelope_from_telegram_update(
        {"update_id": 1},
        correlation_id=cid,
    )
    assert isinstance(r, TelegramAdapterRejected)
    assert r.reason is TelegramAdapterRejectReason.MISSING_MESSAGE
    assert r.correlation_id == cid


def test_update_without_text_rejected() -> None:
    cid = new_correlation_id()
    m = _base_message(text="/x")
    del m["text"]
    r = extract_slice1_envelope_from_telegram_update(_update(message=m), correlation_id=cid)
    assert isinstance(r, TelegramAdapterRejected)
    assert r.reason is TelegramAdapterRejectReason.NON_TEXT_MESSAGE


def test_non_private_chat_rejected() -> None:
    cid = new_correlation_id()
    raw = _update(message=_base_message(text="/start", chat_type="group"))
    r = extract_slice1_envelope_from_telegram_update(raw, correlation_id=cid)
    assert isinstance(r, TelegramAdapterRejected)
    assert r.reason is TelegramAdapterRejectReason.NON_PRIVATE_CHAT


def test_missing_user_id_rejected() -> None:
    cid = new_correlation_id()
    m = _base_message(text="/start")
    m["from"] = {"no_id": True}
    r = extract_slice1_envelope_from_telegram_update(_update(message=m), correlation_id=cid)
    assert isinstance(r, TelegramAdapterRejected)
    assert r.reason is TelegramAdapterRejectReason.INVALID_IDS


def test_missing_update_id_rejected() -> None:
    cid = new_correlation_id()
    raw = {"message": _base_message(text="/status")}
    r = extract_slice1_envelope_from_telegram_update(raw, correlation_id=cid)
    assert isinstance(r, TelegramAdapterRejected)
    assert r.reason is TelegramAdapterRejectReason.MISSING_UPDATE_ID


def test_callback_query_update_accepted() -> None:
    cid = new_correlation_id()
    raw = _update(
        callback_query={"id": "q", "from": {"id": 1}, "data": "buy_vpn"},
    )
    r = extract_slice1_envelope_from_telegram_update(raw, correlation_id=cid)
    assert isinstance(r, TransportIncomingEnvelope)
    assert r.callback_data == "buy_vpn"
    assert r.normalized_command_text is None
    assert r.telegram_user_id == 1


def test_envelope_has_no_raw_payload_fields() -> None:
    names = {f.name for f in fields(TransportIncomingEnvelope)}
    assert "raw" not in names
    assert "raw_update" not in names
    assert "payload" not in names


def test_correlation_id_generated_when_omitted() -> None:
    raw = _update(message=_base_message(text="/start"))
    r = extract_slice1_envelope_from_telegram_update(raw)
    assert isinstance(r, TransportIncomingEnvelope)
    assert is_valid_correlation_id(r.correlation_id)


def test_correlation_id_invalid_rejected_with_fresh_id() -> None:
    r = extract_slice1_envelope_from_telegram_update(
        _update(message=_base_message(text="/start")),
        correlation_id="not-valid",
    )
    assert isinstance(r, TelegramAdapterRejected)
    assert r.reason is TelegramAdapterRejectReason.INVALID_CORRELATION_ID
    assert is_valid_correlation_id(r.correlation_id)


def test_pre_checkout_rejected() -> None:
    cid = new_correlation_id()
    raw = {"update_id": 1, "pre_checkout_query": {"id": "p", "from": {"id": 1}}}
    r = extract_slice1_envelope_from_telegram_update(raw, correlation_id=cid)
    assert isinstance(r, TelegramAdapterRejected)
    assert r.reason is TelegramAdapterRejectReason.UNSUPPORTED_UPDATE_SURFACE


def test_plain_text_not_command_rejected_at_adapter() -> None:
    cid = new_correlation_id()
    raw = _update(message=_base_message(text="hello"))
    r = extract_slice1_envelope_from_telegram_update(raw, correlation_id=cid)
    assert isinstance(r, TelegramAdapterRejected)
    assert r.reason is TelegramAdapterRejectReason.NOT_A_COMMAND
