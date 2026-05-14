"""Tests for ADM-02 ensure-access mutation adapters."""

from __future__ import annotations

from app.admin_support.adm02_ensure_access_mutation import (
    Adm02EnsureAccessIssuanceMutationAdapter,
    _deterministic_ensure_access_idempotency_key,
)
from app.issuance.fake_provider import FakeIssuanceProvider, FakeProviderMode
from app.issuance.service import IssuanceService
from app.shared.test_helpers import run_async as _run


def test_ensure_access_mutation_adapter_idempotent_issue_then_noop() -> None:
    async def main() -> None:
        service = IssuanceService(FakeIssuanceProvider(FakeProviderMode.SUCCESS))
        adapter = Adm02EnsureAccessIssuanceMutationAdapter(service)
        created = await adapter.ensure_access_issued("u-1", correlation_id="0123456789abcdef0123456789abcdef")
        repeated = await adapter.ensure_access_issued("u-1", correlation_id="0123456789abcdef0123456789abcdef")
        assert created is True
        assert repeated is False

    _run(main())


def test_deterministic_idempotency_key_safe_and_non_leaking() -> None:
    key = _deterministic_ensure_access_idempotency_key("u-sensitive-1")
    assert key.startswith("adm02-ensure-access:")
    assert "u-sensitive-1" not in key
    for forbidden in (
        "database_url",
        "postgres://",
        "postgresql://",
        "bearer ",
        "private key",
        "token=",
        "provider_ref",
        "customer_ref",
        "checkout_attempt_id",
        "internal_user_id",
    ):
        assert forbidden not in key.lower()
