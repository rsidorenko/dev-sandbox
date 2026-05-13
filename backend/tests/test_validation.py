"""Pure tests: intent allowlist and bounds."""

import pytest

from app.security.validation import (
    NormalizedIntent,
    ValidationError,
    parse_allowlisted_intent,
    validate_telegram_user_id,
)


def test_parse_allowlisted_intents() -> None:
    assert parse_allowlisted_intent("bootstrap_identity") is NormalizedIntent.BOOTSTRAP_IDENTITY
    assert parse_allowlisted_intent("  get_subscription_status ") is NormalizedIntent.GET_SUBSCRIPTION_STATUS


def test_unknown_intent_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown intent"):
        parse_allowlisted_intent("pay_now")


def test_oversized_intent_rejected() -> None:
    with pytest.raises(ValidationError, match="maximum length"):
        parse_allowlisted_intent("a" * 65)


def test_user_id_bounds() -> None:
    validate_telegram_user_id(1)
    with pytest.raises(ValidationError):
        validate_telegram_user_id(0)
    with pytest.raises(ValidationError):
        validate_telegram_user_id(-1)
