"""Pure tests: slice-1 transport normalization (no Telegram SDK, no IO)."""

from __future__ import annotations

from dataclasses import fields

from app.application.handlers import BootstrapIdentityInput, GetSubscriptionStatusInput
from app.application.telegram_access_resend import TelegramAccessResendSourceCommand
from app.bot_transport.normalized import (
    NormalizationRejectReason,
    NormalizedSlice1Buy,
    NormalizedSlice1Bootstrap,
    NormalizedSlice1Help,
    NormalizedSlice1Menu,
    NormalizedSlice1Plans,
    NormalizedSlice1Rejected,
    NormalizedSlice1ResendAccess,
    NormalizedSlice1Renew,
    NormalizedSlice1Success,
    NormalizedSlice1SupportContact,
    NormalizedSlice1SupportMenu,
    NormalizedSlice1Status,
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


def test_status_maps_to_status_input() -> None:
    cid = new_correlation_id()
    r = parse_slice1_transport(
        TransportIncomingEnvelope(
            telegram_user_id=55,
            correlation_id=cid,
            telegram_update_id=None,
            normalized_command_text="/status",
        ),
    )
    assert isinstance(r, NormalizedSlice1Status)
    assert r.input == GetSubscriptionStatusInput(
        telegram_user_id=55,
        correlation_id=cid,
    )


def test_help_maps_to_read_only_envelope() -> None:
    cid = new_correlation_id()
    r = parse_slice1_transport(
        TransportIncomingEnvelope(
            telegram_user_id=8,
            correlation_id=cid,
            telegram_update_id=None,
            normalized_command_text="/help",
        ),
    )
    assert isinstance(r, NormalizedSlice1Help)
    assert r.correlation_id == cid


def test_help_with_bot_suffix_normalized() -> None:
    cid = new_correlation_id()
    r = parse_slice1_transport(
        TransportIncomingEnvelope(
            telegram_user_id=1,
            correlation_id=cid,
            telegram_update_id=3,
            normalized_command_text="/help@SomeBot",
        ),
    )
    assert isinstance(r, NormalizedSlice1Help)
    assert r.correlation_id == cid


def test_menu_alias_maps_to_store_menu_path() -> None:
    cid = new_correlation_id()
    r = parse_slice1_transport(_env(cid=cid, cmd="/menu", update_id=None))
    assert isinstance(r, NormalizedSlice1Menu)
    assert r.correlation_id == cid


def test_my_subscription_alias_maps_to_status() -> None:
    cid = new_correlation_id()
    r = parse_slice1_transport(_env(cid=cid, cmd="/my_subscription", update_id=None))
    assert isinstance(r, NormalizedSlice1Status)
    assert r.input.correlation_id == cid


def test_storefront_commands_are_normalized() -> None:
    cid = new_correlation_id()
    r_plans = parse_slice1_transport(_env(cid=cid, cmd="/plans", update_id=None))
    r_buy = parse_slice1_transport(_env(cid=cid, cmd="/buy", update_id=None))
    r_checkout = parse_slice1_transport(_env(cid=cid, cmd="/checkout", update_id=None))
    r_success = parse_slice1_transport(_env(cid=cid, cmd="/success", update_id=None))
    r_renew = parse_slice1_transport(_env(cid=cid, cmd="/renew", update_id=None))
    r_support = parse_slice1_transport(_env(cid=cid, cmd="/support", update_id=None))
    r_support_contact = parse_slice1_transport(_env(cid=cid, cmd="/support_contact", update_id=None))
    assert isinstance(r_plans, NormalizedSlice1Plans)
    assert isinstance(r_buy, NormalizedSlice1Buy)
    assert isinstance(r_checkout, NormalizedSlice1Buy)
    assert isinstance(r_success, NormalizedSlice1Success)
    assert isinstance(r_renew, NormalizedSlice1Renew)
    assert isinstance(r_support, NormalizedSlice1SupportMenu)
    assert isinstance(r_support_contact, NormalizedSlice1SupportContact)


def test_resend_access_maps_to_resend_input() -> None:
    cid = new_correlation_id()
    r = parse_slice1_transport(
        TransportIncomingEnvelope(
            telegram_user_id=55,
            correlation_id=cid,
            telegram_update_id=11,
            normalized_command_text="/resend_access",
        ),
    )
    assert isinstance(r, NormalizedSlice1ResendAccess)
    assert r.input.telegram_user_id == 55
    assert r.input.telegram_update_id == 11
    assert r.input.correlation_id == cid
    assert r.input.source_command is TelegramAccessResendSourceCommand.RESEND_ACCESS


def test_get_access_alias_maps_to_resend_input() -> None:
    r = parse_slice1_transport(_env(cmd="/get_access", update_id=123))
    assert isinstance(r, NormalizedSlice1ResendAccess)
    assert r.input.source_command is TelegramAccessResendSourceCommand.GET_ACCESS


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
    assert r.reason is NormalizationRejectReason.MISSING_EVENT_ID_FOR_RESEND


def test_envelope_has_no_raw_payload_field() -> None:
    names = {f.name for f in fields(TransportIncomingEnvelope)}
    assert "raw" not in names
    assert "payload" not in names
    assert "raw_payload" not in names


def test_correlation_id_preserved_on_success() -> None:
    cid = new_correlation_id()
    r = parse_slice1_transport(_env(cid=cid, cmd="/status", update_id=None))
    assert isinstance(r, NormalizedSlice1Status)
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
