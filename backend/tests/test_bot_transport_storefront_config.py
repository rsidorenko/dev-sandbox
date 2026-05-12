from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.bot_transport.storefront_config import (
    build_checkout_url_with_reference,
    load_storefront_public_config,
)
from app.security.checkout_reference import create_signed_checkout_reference, verify_signed_checkout_reference
from app.security.validation import ValidationError


def test_storefront_config_accepts_public_https_urls_and_handle(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_STOREFRONT_CHECKOUT_URL", "https://example.com/checkout")
    monkeypatch.setenv("TELEGRAM_STOREFRONT_RENEWAL_URL", "https://example.com/renew")
    monkeypatch.setenv("TELEGRAM_STOREFRONT_SUPPORT_URL", "https://example.com/support")
    monkeypatch.setenv("TELEGRAM_STOREFRONT_SUPPORT_HANDLE", "@vpn_support")
    cfg = load_storefront_public_config()
    assert cfg.checkout_url == "https://example.com/checkout"
    assert cfg.renewal_url == "https://example.com/renew"
    assert cfg.support_url == "https://example.com/support"
    assert cfg.support_handle == "@vpn_support"


def test_storefront_config_rejects_non_https_and_secret_like_urls(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_STOREFRONT_CHECKOUT_URL", "http://example.com/checkout")
    monkeypatch.setenv("TELEGRAM_STOREFRONT_RENEWAL_URL", "https://example.com/renew?token=abc")
    monkeypatch.setenv("TELEGRAM_STOREFRONT_SUPPORT_URL", "https://example.com/support?api_key=abc")
    cfg = load_storefront_public_config()
    assert cfg.checkout_url is None
    assert cfg.renewal_url is None
    assert cfg.support_url is None


def test_checkout_reference_generation_is_deterministic_and_verifiable() -> None:
    issued_at = datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC)
    signed = create_signed_checkout_reference(
        telegram_user_id=123456,
        internal_user_id="u123456",
        secret="S" * 32,
        now=issued_at,
    )
    verified = verify_signed_checkout_reference(
        reference_id=signed.reference_id,
        reference_proof=signed.reference_proof,
        secret="S" * 32,
        now=issued_at,
    )
    assert verified.telegram_user_id == 123456
    assert verified.internal_user_id == "u123456"


def test_checkout_reference_rejects_expired_reference() -> None:
    signed = create_signed_checkout_reference(
        telegram_user_id=123456,
        internal_user_id="u123456",
        secret="S" * 32,
        now=datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC),
    )
    with pytest.raises(ValidationError, match="expired"):
        verify_signed_checkout_reference(
            reference_id=signed.reference_id,
            reference_proof=signed.reference_proof,
            secret="S" * 32,
            now=datetime(2026, 4, 28, 0, 0, 1, tzinfo=UTC),
            max_age_seconds=7 * 24 * 60 * 60,
        )


def test_checkout_reference_rejects_future_reference() -> None:
    signed = create_signed_checkout_reference(
        telegram_user_id=123456,
        internal_user_id="u123456",
        secret="S" * 32,
        now=datetime(2026, 4, 27, 0, 20, 0, tzinfo=UTC),
    )
    with pytest.raises(ValidationError, match="future"):
        verify_signed_checkout_reference(
            reference_id=signed.reference_id,
            reference_proof=signed.reference_proof,
            secret="S" * 32,
            now=datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC),
            max_age_seconds=7 * 24 * 60 * 60,
            max_future_seconds=60,
        )


def test_checkout_url_appends_reference_params_and_preserves_existing_query() -> None:
    checkout_url = build_checkout_url_with_reference(
        base_url="https://example.com/checkout?plan=monthly",
        client_reference_id="ref-id",
        client_reference_proof="ref-proof",
    )
    assert checkout_url is not None
    assert "plan=monthly" in checkout_url
    assert "client_reference_id=ref-id" in checkout_url
    assert "client_reference_proof=ref-proof" in checkout_url


def test_checkout_url_rejects_unsafe_query_params_in_base_url() -> None:
    checkout_url = build_checkout_url_with_reference(
        base_url="https://example.com/checkout?signature=abc",
        client_reference_id="ref-id",
        client_reference_proof="ref-proof",
    )
    assert checkout_url is None
