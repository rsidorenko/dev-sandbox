"""Pure tests: slice-1 transport normalization (no Telegram SDK, no IO)."""

from __future__ import annotations

from dataclasses import fields

from app.application.handlers import BootstrapIdentityInput
from app.bot_transport.normalized import (
    NormalizationRejectReason,
    NormalizedSlice1Bootstrap,
    NormalizedSlice1Rejected,
    TransportIncomingEnvelope,
    normalize_command_token,
    parse_slice1_transport,
)
from app.shared.correlation import new_correlation_id


def _env(
    *,
    tg: int = 42,
    cid: str | None = None,
    update_id: int | None = 7,
    cmd: str | None = "/start",
) -> TransportIncomingEnvelope:
    return TransportIncomingEnvelope(
        telegram_user_id=tg,
        correlation_id=cid or new_correlation_id(),
        telegram_update_id=update_id,
        normalized_command_text=cmd,
    )


def test_start_maps_to_bootstrap_input() -> None:
    cid = new_correlation_id()
    r = parse_slice1_transport(_env(cid=cid, cmd="/start", update_id=100))
    assert isinstance(r, NormalizedSlice1Bootstrap)
    assert r.input == BootstrapIdentityInput(
        telegram_user_id=42,
        telegram_update_id=100,
        correlation_id=cid,
    )


def test_start_with_bot_suffix_normalized() -> None:
    cid = new_correlation_id()
    r = parse_slice1_transport(
        TransportIncomingEnvelope(
            telegram_user_id=99,
            correlation_id=cid,
            telegram_update_id=1,
            normalized_command_text="/start@SomeBot",
        ),
    )
    assert isinstance(r, NormalizedSlice1Bootstrap)
    assert r.input.telegram_user_id == 99


def test_status_rejected_as_unknown() -> None:
    r = parse_slice1_transport(
        TransportIncomingEnvelope(
            telegram_user_id=55,
            correlation_id=new_correlation_id(),
            telegram_update_id=None,
            normalized_command_text="/status",
        ),
    )
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.UNKNOWN_COMMAND


def test_help_rejected_as_unknown() -> None:
    r = parse_slice1_transport(
        TransportIncomingEnvelope(
            telegram_user_id=8,
            correlation_id=new_correlation_id(),
            telegram_update_id=None,
            normalized_command_text="/help",
        ),
    )
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.UNKNOWN_COMMAND


def test_help_with_bot_suffix_rejected_as_unknown() -> None:
    r = parse_slice1_transport(
        TransportIncomingEnvelope(
            telegram_user_id=1,
            correlation_id=new_correlation_id(),
            telegram_update_id=3,
            normalized_command_text="/help@SomeBot",
        ),
    )
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.UNKNOWN_COMMAND


def test_menu_rejected_as_unknown() -> None:
    r = parse_slice1_transport(_env(cmd="/menu", update_id=None))
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.UNKNOWN_COMMAND


def test_my_subscription_rejected_as_unknown() -> None:
    r = parse_slice1_transport(_env(cmd="/my_subscription", update_id=None))
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.UNKNOWN_COMMAND


def test_all_removed_commands_rejected() -> None:
    for cmd in ("/plans", "/buy", "/checkout", "/success", "/renew", "/support", "/support_contact"):
        r = parse_slice1_transport(_env(cmd=cmd, update_id=None))
        assert isinstance(r, NormalizedSlice1Rejected), f"{cmd} should be rejected"
        assert r.reason is NormalizationRejectReason.UNKNOWN_COMMAND


def test_resend_access_rejected_as_unknown() -> None:
    r = parse_slice1_transport(
        TransportIncomingEnvelope(
            telegram_user_id=55,
            correlation_id=new_correlation_id(),
            telegram_update_id=11,
            normalized_command_text="/resend_access",
        ),
    )
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.UNKNOWN_COMMAND


def test_get_access_rejected_as_unknown() -> None:
    r = parse_slice1_transport(_env(cmd="/get_access", update_id=123))
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.UNKNOWN_COMMAND


def test_unknown_command_rejected() -> None:
    r = parse_slice1_transport(_env(cmd="/unknown"))
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.UNKNOWN_COMMAND


def test_invalid_telegram_user_id_rejected() -> None:
    r = parse_slice1_transport(_env(tg=0, cmd="/start"))
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.INVALID_INPUT


def test_missing_update_id_rejected_for_bootstrap() -> None:
    r = parse_slice1_transport(_env(update_id=None, cmd="/start"))
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.MISSING_EVENT_ID_FOR_BOOTSTRAP


def test_invalid_update_id_rejected_for_bootstrap() -> None:
    r = parse_slice1_transport(_env(update_id=-1, cmd="/start"))
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.INVALID_INPUT


def test_missing_update_id_rejected_for_resend() -> None:
    r = parse_slice1_transport(_env(update_id=None, cmd="/resend_access"))
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.UNKNOWN_COMMAND


def test_envelope_has_no_raw_payload_field() -> None:
    names = {f.name for f in fields(TransportIncomingEnvelope)}
    assert "raw" not in names
    assert "payload" not in names
    assert "raw_payload" not in names


def test_correlation_id_preserved_on_start_success() -> None:
    cid = new_correlation_id()
    r = parse_slice1_transport(_env(cid=cid, cmd="/start", update_id=100))
    assert isinstance(r, NormalizedSlice1Bootstrap)
    assert r.input.correlation_id == cid


def test_non_slice1_commands_rejected() -> None:
    for cmd in ("/admin", "/pay", "/billing", "/issue"):
        r = parse_slice1_transport(_env(cmd=cmd))
        assert isinstance(r, NormalizedSlice1Rejected)
        assert r.reason is NormalizationRejectReason.UNKNOWN_COMMAND


def test_normalize_command_token_first_line_only() -> None:
    assert normalize_command_token("  /STATUS  extra junk  ") == "/status"


def test_invalid_correlation_rejected() -> None:
    r = parse_slice1_transport(_env(cid="not-a-hex-id", cmd="/status", update_id=None))
    assert isinstance(r, NormalizedSlice1Rejected)
    assert r.reason is NormalizationRejectReason.INVALID_INPUT
