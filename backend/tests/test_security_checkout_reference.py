"""Tests for security.checkout_reference: signed reference create/verify."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.security.checkout_reference import (
    DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS,
    SignedCheckoutReference,
    create_signed_checkout_reference,
    verify_signed_checkout_reference,
)
from app.security.validation import ValidationError

_SECRET = "test-secret-key-12345"


def _create(**overrides) -> SignedCheckoutReference:
    defaults = {
        "telegram_user_id": 123456789,
        "internal_user_id": "int-uuid-001",
        "secret": _SECRET,
    }
    defaults.update(overrides)
    return create_signed_checkout_reference(**defaults)


# --- create ---


def test_create_basic_fields():
    ref = _create()
    assert ref.reference_id
    assert ref.reference_proof
    assert ref.payload.telegram_user_id == 123456789
    assert ref.payload.internal_user_id == "int-uuid-001"
    assert ref.payload.schema_version == 1


def test_create_without_internal_user_id():
    ref = _create(internal_user_id=None)
    assert ref.payload.internal_user_id is None


def test_create_normalizes_whitespace_internal_user_id():
    ref = _create(internal_user_id="  abc  ")
    assert ref.payload.internal_user_id == "abc"


def test_create_empty_string_internal_user_id_becomes_none():
    ref = _create(internal_user_id="   ")
    assert ref.payload.internal_user_id is None


def test_create_uses_provided_now():
    dt = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    ref = _create(now=dt)
    assert ref.payload.issued_at.startswith("2026-01-15T10:30:00")


def test_create_strips_microseconds():
    dt = datetime(2026, 3, 1, 12, 0, 0, 999999, tzinfo=UTC)
    ref = _create(now=dt)
    assert ".999" not in ref.payload.issued_at


# --- round-trip (create + verify) ---


def test_round_trip_success():
    ref = _create()
    payload = verify_signed_checkout_reference(
        reference_id=ref.reference_id,
        reference_proof=ref.reference_proof,
        secret=_SECRET,
        now=datetime.now(UTC),
    )
    assert payload.telegram_user_id == 123456789
    assert payload.internal_user_id == "int-uuid-001"


def test_round_trip_no_internal_user():
    ref = _create(internal_user_id=None)
    payload = verify_signed_checkout_reference(
        reference_id=ref.reference_id,
        reference_proof=ref.reference_proof,
        secret=_SECRET,
    )
    assert payload.internal_user_id is None


# --- verify: signature checks ---


def test_verify_wrong_secret():
    ref = _create()
    with pytest.raises(ValidationError, match="invalid"):
        verify_signed_checkout_reference(
            reference_id=ref.reference_id,
            reference_proof=ref.reference_proof,
            secret="wrong-secret",
        )


def test_verify_tampered_proof():
    ref = _create()
    with pytest.raises(ValidationError, match="invalid"):
        verify_signed_checkout_reference(
            reference_id=ref.reference_id,
            reference_proof="deadbeef" * 8,
            secret=_SECRET,
        )


def test_verify_tampered_reference_id():
    ref = _create()
    with pytest.raises(ValidationError, match="invalid"):
        verify_signed_checkout_reference(
            reference_id=ref.reference_id + "x",
            reference_proof=ref.reference_proof,
            secret=_SECRET,
        )


# --- verify: required fields ---


def test_verify_empty_reference_id():
    with pytest.raises(ValidationError, match="required"):
        verify_signed_checkout_reference(
            reference_id="  ",
            reference_proof="abc",
            secret=_SECRET,
        )


def test_verify_empty_reference_proof():
    with pytest.raises(ValidationError, match="required"):
        verify_signed_checkout_reference(
            reference_id="abc",
            reference_proof="  ",
            secret=_SECRET,
        )


def test_verify_too_long_reference_id():
    with pytest.raises(ValidationError, match="maximum length"):
        verify_signed_checkout_reference(
            reference_id="x" * 2049,
            reference_proof="abc",
            secret=_SECRET,
        )


def test_verify_too_long_reference_proof():
    with pytest.raises(ValidationError, match="maximum length"):
        verify_signed_checkout_reference(
            reference_id="abc",
            reference_proof="x" * 257,
            secret=_SECRET,
        )


# --- verify: expiry ---


def test_verify_expired_reference():
    dt = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
    ref = _create(now=dt)
    with pytest.raises(ValidationError, match="expired"):
        verify_signed_checkout_reference(
            reference_id=ref.reference_id,
            reference_proof=ref.reference_proof,
            secret=_SECRET,
            now=datetime.now(UTC),
        )


def test_verify_future_reference():
    future = datetime.now(UTC) + timedelta(days=365)
    ref = _create(now=future)
    with pytest.raises(ValidationError, match="future"):
        verify_signed_checkout_reference(
            reference_id=ref.reference_id,
            reference_proof=ref.reference_proof,
            secret=_SECRET,
        )


def test_verify_near_expiry_still_valid():
    """Reference just within max_age should pass."""
    now = datetime.now(UTC)
    issued = now - timedelta(seconds=DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS - 10)
    ref = _create(now=issued)
    payload = verify_signed_checkout_reference(
        reference_id=ref.reference_id,
        reference_proof=ref.reference_proof,
        secret=_SECRET,
        now=now,
    )
    assert payload.telegram_user_id == 123456789


# --- verify: invalid payloads ---


def test_verify_invalid_base64():
    """Invalid base64 fails proof check first (proof is validated before decoding)."""
    with pytest.raises(ValidationError, match="invalid"):
        verify_signed_checkout_reference(
            reference_id="not-base64!!!",
            reference_proof="abc",
            secret=_SECRET,
        )


def test_verify_invalid_json_in_payload():
    import base64

    raw = base64.urlsafe_b64encode(b"not-json").decode()
    # Need a valid proof for this reference_id first
    import hashlib
    import hmac

    proof = hmac.new(_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(ValidationError, match="not valid"):
        verify_signed_checkout_reference(
            reference_id=raw,
            reference_proof=proof,
            secret=_SECRET,
        )


# --- verify: parameter validation ---


def test_verify_max_age_must_be_positive():
    with pytest.raises(ValidationError, match="positive"):
        verify_signed_checkout_reference(
            reference_id="x",
            reference_proof="y",
            secret=_SECRET,
            max_age_seconds=0,
        )


def test_verify_max_future_must_be_non_negative():
    with pytest.raises(ValidationError, match="non-negative"):
        verify_signed_checkout_reference(
            reference_id="x",
            reference_proof="y",
            secret=_SECRET,
            max_future_seconds=-1,
        )
