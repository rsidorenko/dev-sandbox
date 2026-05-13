"""Unit tests for :class:`Adm01PostgresSubscriptionReadAdapter` (fakes only; no I/O)."""

from __future__ import annotations

import pytest

from app.admin_support.adm01_postgres_subscription_read_adapter import (
    Adm01PostgresSubscriptionReadAdapter,
)
from app.application.interfaces import SubscriptionSnapshot
from app.security.errors import InternalErrorCategory, PersistenceDependencyError

_RAW_MARKER = "raw-billing-payload-should-not-leak"


class _FakeSnapshots:
    def __init__(self, current: SubscriptionSnapshot | None) -> None:
        self._current = current
        self.last_user: str | None = None

    async def get_for_user(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        self.last_user = internal_user_id
        return self._current


class _ErrorSnapshots:
    def __init__(self, err: Exception) -> None:
        self._err = err

    async def get_for_user(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        del internal_user_id
        raise self._err


@pytest.mark.asyncio
async def test_missing_snapshot_returns_none() -> None:
    a = Adm01PostgresSubscriptionReadAdapter(_FakeSnapshots(None))
    s = await a.get_subscription_snapshot("u-1")
    assert s is None


@pytest.mark.asyncio
async def test_existing_snapshot_passthrough() -> None:
    snap = SubscriptionSnapshot(internal_user_id="u-1", state_label="active")
    a = Adm01PostgresSubscriptionReadAdapter(_FakeSnapshots(snap))
    s = await a.get_subscription_snapshot("u-1")
    assert s is not None
    assert s.internal_user_id == "u-1"
    assert s.state_label == "active"
    # Guard against accidental expansion to raw payload-like debug fields.
    assert _RAW_MARKER not in repr(s) + str(s)


@pytest.mark.asyncio
async def test_persistence_dependency_error_propagates() -> None:
    a = Adm01PostgresSubscriptionReadAdapter(
        _ErrorSnapshots(
            PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT),
        ),
    )
    with pytest.raises(PersistenceDependencyError) as e:
        await a.get_subscription_snapshot("u-1")
    assert e.value.category is InternalErrorCategory.PERSISTENCE_TRANSIENT
