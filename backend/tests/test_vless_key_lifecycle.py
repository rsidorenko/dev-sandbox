"""Tests for VLESS key lifecycle: fulfillment key management, permanent subscription URL."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from app.issuance.vless_provider import VlessProviderOutcome
from app.runtime.fulfillment_processor import _ensure_vless_keys_after_payment
from app.shared.test_helpers import run_async as _run


# ─── Helpers ──────────────────────────────────────────────────────────


class _Record(dict):
    """asyncpg.Record-like: supports both dict access and attribute access."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


class _FakePool:
    def __init__(self, row=None):
        self._row = row
        self.execute_log = []

    async def fetchrow(self, sql, *args):
        return self._row

    async def execute(self, sql, *args):
        self.execute_log.append(sql)


# ─── _ensure_vless_keys_after_payment ─────────────────────────────────


def test_keys_deleted_calls_create_user():
    pool = _FakePool(row=_Record(keys_deactivated_at=datetime.now(UTC), keys_deleted_at=datetime.now(UTC), device_count=5))
    provider = AsyncMock()
    provider.create_user = AsyncMock(return_value=MagicMock(outcome=VlessProviderOutcome.SUCCESS))

    _run(_ensure_vless_keys_after_payment(pool=pool, vless_provider=provider, internal_user_id="u1"))

    provider.create_user.assert_called_once_with(internal_user_id="u1", device_count=5)
    provider.activate_user.assert_not_called()


def test_keys_deactivated_calls_activate_user():
    pool = _FakePool(row=_Record(keys_deactivated_at=datetime.now(UTC), keys_deleted_at=None, device_count=5))
    provider = AsyncMock()
    provider.activate_user = AsyncMock(return_value=MagicMock(outcome=VlessProviderOutcome.SUCCESS))

    _run(_ensure_vless_keys_after_payment(pool=pool, vless_provider=provider, internal_user_id="u1"))

    provider.activate_user.assert_called_once_with(internal_user_id="u1", device_count=5)
    provider.create_user.assert_not_called()


def test_no_lifecycle_flags_calls_create_user():
    pool = _FakePool(row=_Record(keys_deactivated_at=None, keys_deleted_at=None, device_count=5))
    provider = AsyncMock()
    provider.create_user = AsyncMock(return_value=MagicMock(outcome=VlessProviderOutcome.SUCCESS))

    _run(_ensure_vless_keys_after_payment(pool=pool, vless_provider=provider, internal_user_id="u1"))

    provider.create_user.assert_called_once_with(internal_user_id="u1", device_count=5)


def test_resets_lifecycle_flags():
    pool = _FakePool(row=_Record(keys_deactivated_at=datetime.now(UTC), keys_deleted_at=datetime.now(UTC), device_count=5))
    provider = AsyncMock()
    provider.create_user = AsyncMock(return_value=MagicMock(outcome=VlessProviderOutcome.SUCCESS))

    _run(_ensure_vless_keys_after_payment(pool=pool, vless_provider=provider, internal_user_id="u1"))

    assert any("keys_deactivated_at = NULL" in sql for sql in pool.execute_log)


def test_provider_failure_does_not_raise():
    pool = _FakePool(row=_Record(keys_deactivated_at=datetime.now(UTC), keys_deleted_at=None, device_count=5))
    provider = AsyncMock()
    provider.activate_user = AsyncMock(side_effect=RuntimeError("panel down"))

    _run(_ensure_vless_keys_after_payment(pool=pool, vless_provider=provider, internal_user_id="u1"))


def test_no_snapshot_creates_user():
    """No snapshot row means new user — create_user is called."""
    pool = _FakePool(row=None)
    provider = AsyncMock()
    provider.create_user = AsyncMock(return_value=MagicMock(outcome=VlessProviderOutcome.SUCCESS))

    _run(_ensure_vless_keys_after_payment(pool=pool, vless_provider=provider, internal_user_id="u1"))

    provider.create_user.assert_called_once_with(internal_user_id="u1", device_count=0)


def test_device_count_propagated_to_create_user():
    """device_count from subscription_snapshots is passed to create_user."""
    pool = _FakePool(row=_Record(keys_deactivated_at=None, keys_deleted_at=None, device_count=10))
    provider = AsyncMock()
    provider.create_user = AsyncMock(return_value=MagicMock(outcome=VlessProviderOutcome.SUCCESS))

    _run(_ensure_vless_keys_after_payment(pool=pool, vless_provider=provider, internal_user_id="u1"))

    provider.create_user.assert_called_once_with(internal_user_id="u1", device_count=10)


def test_device_count_propagated_to_activate_user():
    """device_count from subscription_snapshots is passed to activate_user."""
    pool = _FakePool(row=_Record(keys_deactivated_at=datetime.now(UTC), keys_deleted_at=None, device_count=3))
    provider = AsyncMock()
    provider.activate_user = AsyncMock(return_value=MagicMock(outcome=VlessProviderOutcome.SUCCESS))

    _run(_ensure_vless_keys_after_payment(pool=pool, vless_provider=provider, internal_user_id="u1"))

    provider.activate_user.assert_called_once_with(internal_user_id="u1", device_count=3)


def test_device_count_null_falls_back_to_zero():
    """NULL device_count in DB falls back to 0 (provider uses trial default)."""
    pool = _FakePool(row=_Record(keys_deactivated_at=None, keys_deleted_at=None, device_count=None))
    provider = AsyncMock()
    provider.create_user = AsyncMock(return_value=MagicMock(outcome=VlessProviderOutcome.SUCCESS))

    _run(_ensure_vless_keys_after_payment(pool=pool, vless_provider=provider, internal_user_id="u1"))

    provider.create_user.assert_called_once_with(internal_user_id="u1", device_count=0)


# ─── Permanent subscription URL ───────────────────────────────────────


def test_ensure_subscription_token_returns_existing_without_expiry_check():
    """Token returned as-is even if subscription_token_expires_at is in the past."""
    from app.issuance.xui_vless_provider import _ensure_subscription_token

    expired_row = _Record(subscription_token="existing-token-abc")

    class _Pool:
        async def fetchrow(self, sql, uid):
            return expired_row
        async def execute(self, *a):
            pass

    result = _run(_ensure_subscription_token(_Pool(), "u1"))
    assert result == "existing-token-abc"


def test_ensure_subscription_token_generates_when_none():
    """New token created only when no token exists in DB."""
    from app.issuance.xui_vless_provider import _ensure_subscription_token

    empty_row = _Record(subscription_token=None)

    executed = []

    class _Pool:
        async def fetchrow(self, sql, uid):
            return empty_row
        async def execute(self, sql, *args):
            executed.append(args)

    result = _run(_ensure_subscription_token(_Pool(), "u1"))
    assert result is not None
    assert len(result) > 10
    assert len(executed) == 1


def test_ensure_subscription_token_generates_when_no_row():
    """New token created when user has no row at all."""
    from app.issuance.xui_vless_provider import _ensure_subscription_token

    executed = []

    class _Pool:
        async def fetchrow(self, sql, uid):
            return None
        async def execute(self, sql, *args):
            executed.append(args)

    result = _run(_ensure_subscription_token(_Pool(), "u1"))
    assert result is not None
    assert len(result) > 10
