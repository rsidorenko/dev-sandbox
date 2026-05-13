"""Narrow tests for slice-1 PostgreSQL composition wiring (no real Postgres)."""

from __future__ import annotations

import asyncio

import pytest

from app.application.telegram_update_dedup import InMemoryTelegramUpdateDedupGuard
from app.persistence.in_memory import (
    InMemoryAuditAppender,
    InMemoryIdempotencyRepository,
    InMemoryOutboundDeliveryLedger,
    InMemorySubscriptionSnapshotReader,
    InMemoryUserIdentityRepository,
)
from app.persistence.postgres_audit import PostgresAuditAppender
from app.persistence.postgres_idempotency import PostgresIdempotencyRepository
from app.persistence.postgres_outbound_delivery import PostgresOutboundDeliveryLedger
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_telegram_update_dedup import PostgresTelegramUpdateDedupGuard
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository
from app.persistence.slice1_postgres_wiring import (
    resolve_slice1_composition_for_runtime,
    slice1_postgres_repos_requested,
)
from app.security.config import ConfigurationError, RuntimeConfig


def _cfg() -> RuntimeConfig:
    return RuntimeConfig(
        bot_token="1234567890tok",
        database_url="postgresql://localhost/testdb",
        app_env="development",
        debug_safe=False,
    )


def test_slice1_postgres_flag_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLICE1_USE_POSTGRES_REPOS", raising=False)
    assert slice1_postgres_repos_requested() is False


def test_slice1_postgres_flag_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", "1")
    assert slice1_postgres_repos_requested() is True


@pytest.mark.parametrize(
    "raw",
    ("1", "true", "TRUE", " yes "),
)
def test_slice1_postgres_repos_requested_truthy_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)
    assert slice1_postgres_repos_requested() is True


@pytest.mark.parametrize(
    "raw",
    ("", "0", "false", "no", "random"),
)
def test_slice1_postgres_repos_requested_falsey_when_env_set(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", raw)
    assert slice1_postgres_repos_requested() is False


def test_resolve_without_flag_uses_in_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLICE1_USE_POSTGRES_REPOS", raising=False)

    async def main() -> None:
        composition, pool = await resolve_slice1_composition_for_runtime(_cfg())
        assert pool is None
        assert isinstance(composition.identity, InMemoryUserIdentityRepository)
        assert isinstance(composition.idempotency, InMemoryIdempotencyRepository)
        assert isinstance(composition.snapshots, InMemorySubscriptionSnapshotReader)
        assert isinstance(composition.audit, InMemoryAuditAppender)
        assert isinstance(composition.outbound_delivery, InMemoryOutboundDeliveryLedger)
        assert isinstance(composition.telegram_update_dedup, InMemoryTelegramUpdateDedupGuard)

    asyncio.run(main())


def test_resolve_with_flag_and_pool_uses_postgres_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", "1")

    class _FakePool:
        pass

    async def fake_open(_dsn: str) -> _FakePool:
        return _FakePool()

    async def main() -> None:
        composition, pool = await resolve_slice1_composition_for_runtime(_cfg(), open_pool=fake_open)
        assert pool is not None
        assert isinstance(composition.identity, PostgresUserIdentityRepository)
        assert isinstance(composition.idempotency, PostgresIdempotencyRepository)
        assert isinstance(composition.snapshots, PostgresSubscriptionSnapshotReader)
        assert isinstance(composition.audit, PostgresAuditAppender)
        assert isinstance(composition.outbound_delivery, PostgresOutboundDeliveryLedger)
        assert isinstance(composition.telegram_update_dedup, PostgresTelegramUpdateDedupGuard)

    asyncio.run(main())


def test_resolve_raises_configuration_error_when_flag_on_and_database_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", "1")
    cfg = RuntimeConfig(
        bot_token="1234567890tok",
        database_url=None,
        app_env="development",
        debug_safe=False,
    )

    async def should_not_open(_dsn: str) -> object:
        raise AssertionError("open_pool must not be called without DATABASE_URL")

    async def main() -> None:
        with pytest.raises(ConfigurationError, match="DATABASE_URL"):
            await resolve_slice1_composition_for_runtime(cfg, open_pool=should_not_open)

    asyncio.run(main())


def test_resolve_raises_when_pool_open_fails_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLICE1_USE_POSTGRES_REPOS", "1")

    async def failing_open(_dsn: str) -> object:
        raise OSError("no database")

    async def main() -> None:
        with pytest.raises(OSError, match="no database"):
            await resolve_slice1_composition_for_runtime(_cfg(), open_pool=failing_open)

    asyncio.run(main())
