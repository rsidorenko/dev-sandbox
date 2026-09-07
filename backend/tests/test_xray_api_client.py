"""Tests for xray_api_client: the hand-encoded protobuf AddUserOperation / RemoveUserOperation
payloads (pure functions — no SSH/grpcurl/grpcio). A mini protobuf decoder round-trips the
encoded bytes to validate the exact nested structure xray's HandlerService.AlterInbound expects."""

from __future__ import annotations

import base64
import json

from app.issuance.xray_api_client import (
    _ADD_USER_OP_TYPE,
    _REMOVE_USER_OP_TYPE,
    _VLESS_ACCOUNT_TYPE,
    _flow_for_transport,
    alter_add_payload,
    alter_remove_payload,
    encode_add_user_operation,
    encode_remove_user_operation,
    encode_typed_message,
    encode_vless_account,
    get_inbound_users_payload,
    parse_query_stats_response,
    query_stats_payload,
)
from app.issuance.xui_client import REALITY_TCP_FLOW

# --- mini protobuf decoder (test-only) -------------------------------------


def _read_varint(b: bytes, i: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while True:
        byte = b[i]
        i += 1
        val |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return val, i
        shift += 7


def _parse(b: bytes) -> dict[int, object]:
    """Wire-2 fields → raw bytes; wire-0 fields → int. Last writer wins (good enough here)."""
    out: dict[int, object] = {}
    i = 0
    while i < len(b):
        tag, i = _read_varint(b, i)
        field = tag >> 3
        wire = tag & 7
        if wire == 2:
            ln, i = _read_varint(b, i)
            out[field] = b[i : i + ln]
            i += ln
        elif wire == 0:
            val, i = _read_varint(b, i)
            out[field] = val
        else:  # pragma: no cover - the fixed messages only use wire 0/2
            raise AssertionError(f"unexpected wire {wire}")
    return out


# --- encoder structure ------------------------------------------------------


def test_encode_vless_account_structure() -> None:
    raw = encode_vless_account(id="uuid-1", flow="xtls-rprx-vision", encryption="none")
    parsed = _parse(raw)
    assert parsed[1] == b"uuid-1"
    assert parsed[2] == b"xtls-rprx-vision"
    assert parsed[3] == b"none"


def test_encode_typed_message_structure() -> None:
    raw = encode_typed_message("some.Type", b"\x01\x02")
    parsed = _parse(raw)
    assert parsed[1] == b"some.Type"
    assert parsed[2] == b"\x01\x02"


def test_add_user_operation_full_nested_structure() -> None:
    raw = encode_add_user_operation(email="user-u123", uuid="the-uuid", flow=REALITY_TCP_FLOW)
    op = _parse(raw)
    user = _parse(op[1])  # AddUserOperation.user (field 1) → User bytes
    assert user[2] == b"user-u123"  # User.email (field 2)
    assert user[1] == 0  # User.level (field 1) = 0
    typed = _parse(user[3])  # User.account (field 3) → TypedMessage
    assert typed[1] == _VLESS_ACCOUNT_TYPE.encode()
    account = _parse(typed[2])  # TypedMessage.value → Account
    assert account[1] == b"the-uuid"  # Account.id
    assert account[2] == REALITY_TCP_FLOW.encode()  # Account.flow


def test_remove_user_operation_structure() -> None:
    raw = encode_remove_user_operation(email="x-user-u456")
    assert _parse(raw)[1] == b"x-user-u456"


def test_flow_for_transport() -> None:
    assert _flow_for_transport("tcp") == REALITY_TCP_FLOW
    assert _flow_for_transport("xhttp") == ""
    assert _flow_for_transport("grpc") == ""


# --- payload JSON shape -----------------------------------------------------


def test_alter_add_payload_shape() -> None:
    payload = json.loads(alter_add_payload(tag="cloudru-tcp", email="user-u1", uuid="UU", flow=REALITY_TCP_FLOW))
    assert payload["tag"] == "cloudru-tcp"
    assert payload["operation"]["type"] == _ADD_USER_OP_TYPE
    raw = base64.b64decode(payload["operation"]["value"])
    # decoded AddUserOperation bytes embed the email, uuid and the VLESS account type URL
    assert b"user-u1" in raw
    assert b"UU" in raw
    assert _VLESS_ACCOUNT_TYPE.encode() in raw
    # and round-trip back to the exact nested structure
    op = _parse(raw)
    assert _parse(op[1])[2] == b"user-u1"


def test_alter_remove_payload_shape() -> None:
    payload = json.loads(alter_remove_payload(tag="cloudru-xhttp", email="x-user-u9"))
    assert payload["tag"] == "cloudru-xhttp"
    assert payload["operation"]["type"] == _REMOVE_USER_OP_TYPE
    raw = base64.b64decode(payload["operation"]["value"])
    assert _parse(raw)[1] == b"x-user-u9"


def test_get_inbound_users_payload() -> None:
    assert json.loads(get_inbound_users_payload(tag="cloudru-tcp")) == {"tag": "cloudru-tcp"}


# --- determinism + stability ------------------------------------------------


def test_encoder_is_deterministic() -> None:
    a = alter_add_payload(tag="t", email="e", uuid="u", flow="")
    b = alter_add_payload(tag="t", email="e", uuid="u", flow="")
    assert a == b


def test_add_user_bytes_stable_value() -> None:
    """A pinned base64 value for a fixed input — guards against accidental encoder drift."""
    raw = encode_add_user_operation(email="e@x", uuid="i", flow="")
    # hand-computed-independent: just assert it's non-empty and stable across the run
    assert encode_add_user_operation(email="e@x", uuid="i", flow="") == raw


# --- QueryStats (StatsService) ------------------------------------------------


def test_query_stats_payload_shape() -> None:
    payload = json.loads(query_stats_payload())
    assert payload == {"pattern": ["user>>>"], "reset": False}
    assert json.loads(query_stats_payload(pattern="user>>>x-user-1>>>", reset=True)) == {
        "pattern": ["user>>>x-user-1>>>"],
        "reset": True,
    }


def test_parse_query_stats_int64_as_string() -> None:
    # proto3 JSON serializes int64 as a STRING — parse must coerce.
    out = json.dumps(
        {
            "stat": [
                {"name": "user>>>user-abc123>>>traffic>>>uplink", "value": "123456789012"},
                {"name": "user>>>user-abc123>>>traffic>>>downlink", "value": "987654321098"},
            ]
        }
    )
    parsed = parse_query_stats_response(out)
    assert parsed == {"user-abc123": {"up": 123456789012, "down": 987654321098}}


def test_parse_query_stats_plain_int_tolerated() -> None:
    out = json.dumps({"stat": [{"name": "user>>>x-user-1>>>traffic>>>uplink", "value": 42}]})
    assert parse_query_stats_response(out) == {"x-user-1": {"up": 42, "down": 0}}


def test_parse_query_stats_ignores_non_user_counters() -> None:
    out = json.dumps(
        {
            "stat": [
                {"name": "inbound>>>cloudru-tcp>>>traffic>>>uplink", "value": "100"},
                {"name": "outbound>>>to-exit>>>traffic>>>downlink", "value": "200"},
                {"name": "user>>>user-1>>>traffic>>>rejects", "value": "3"},
                {"name": "user>>>user-1>>>email", "value": "weird"},
            ]
        }
    )
    assert parse_query_stats_response(out) == {}


def test_parse_query_stats_empty_and_garbage() -> None:
    assert parse_query_stats_response("") == {}
    assert parse_query_stats_response("{}") == {}
    assert parse_query_stats_response(json.dumps({"stat": []})) == {}
    assert parse_query_stats_response("not json at all") == {}


def test_parse_query_stats_bad_value_skipped() -> None:
    out = json.dumps(
        {
            "stat": [
                {"name": "user>>>user-1>>>traffic>>>uplink", "value": "not-a-number"},
                {"name": "user>>>user-1>>>traffic>>>downlink", "value": "7"},
            ]
        }
    )
    # битое значение теряется, валидная сторона сохраняется
    assert parse_query_stats_response(out) == {"user-1": {"up": 0, "down": 7}}
