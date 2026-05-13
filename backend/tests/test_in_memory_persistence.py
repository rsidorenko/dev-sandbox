"""Tests for in-memory persistence adapters (slice 1)."""

from __future__ import annotations

import asyncio

import pytest

from app.application.interfaces import AuditEvent, SubscriptionSnapshot
from app.persistence.in_memory import (
    InMemoryAuditAppender,
    InMemoryIdempotencyRepository,
    InMemorySubscriptionSnapshotReader,
    InMemoryUserIdentityRepository,
)
from app.security.errors import InternalErrorCategory
from app.shared.types import OperationOutcomeCategory


def test_identity_creates_once_and_reuses() -> None:
    async def main() -> None:
        r = InMemoryUserIdentityRepository()
        a = await r.create_if_absent(42)
        b = await r.create_if_absent(42)
        assert a.internal_user_id == b.internal_user_id == "u42"
        found = await r.find_by_telegram_user_id(42)
        assert found is not None
        assert found.telegram_user_id == 42

    asyncio.run(main())


def test_idempotency_begin_or_get_and_complete() -> None:
    async def main() -> None:
        repo = InMemoryIdempotencyRepository()
        k = "abc"
        assert await repo.get(k) is None
        r1 = await repo.begin_or_get(k)
        assert r1.key == k and r1.completed is False
        r2 = await repo.begin_or_get(k)
        assert r2.completed is False
        await repo.complete(k)
        r3 = await repo.begin_or_get(k)
        assert r3.completed is True
        g = await repo.get(k)
        assert g is not None and g.completed is True

    asyncio.run(main())


def test_audit_append_only() -> None:
    async def main() -> None:
        a = InMemoryAuditAppender()
        e1 = AuditEvent(
            correlation_id="a" * 32,
            operation="uc01_bootstrap_identity",
            outcome=OperationOutcomeCategory.SUCCESS,
            internal_category=None,
        )
        await a.append(e1)
        await a.append(
            AuditEvent(
                correlation_id="b" * 32,
                operation="uc01_bootstrap_identity",
                outcome=OperationOutcomeCategory.INTERNAL_FAILURE,
                internal_category=InternalErrorCategory.UNKNOWN,
            )
        )
        events = await a.recorded_events()
        assert len(events) == 2
        assert events[0].correlation_id == e1.correlation_id
        events2 = await a.recorded_events()
        assert len(events2) == 2

    asyncio.run(main())


def test_snapshot_reader_configured_or_none() -> None:
    async def main() -> None:
        snap = SubscriptionSnapshot(internal_user_id="u1", state_label="inactive")
        r = InMemorySubscriptionSnapshotReader({"u1": snap})
        got = await r.get_for_user("u1")
        assert got is not None
        assert got.state_label == "inactive"
        assert await r.get_for_user("missing") is None

    asyncio.run(main())


def test_snapshot_returns_copy_not_alias() -> None:
    async def main() -> None:
        inner = SubscriptionSnapshot(internal_user_id="u9", state_label="inactive")
        r = InMemorySubscriptionSnapshotReader({"u9": inner})
        out = await r.get_for_user("u9")
        assert out is not None
        assert out is not inner

    asyncio.run(main())


@pytest.mark.parametrize("correlation_id", ["c" * 32])
def test_audit_event_validation(correlation_id: str) -> None:
    """Audit appender stores events with correlation id as provided (hex length from domain)."""

    async def main() -> None:
        a = InMemoryAuditAppender()
        await a.append(
            AuditEvent(
                correlation_id=correlation_id,
                operation="uc01_bootstrap_identity",
                outcome=OperationOutcomeCategory.SUCCESS,
                internal_category=None,
            )
        )
        ev = (await a.recorded_events())[0]
        assert ev.correlation_id == correlation_id

    asyncio.run(main())
