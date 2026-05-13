"""Unit tests for :class:`Adm01PostgresIssuanceReadAdapter` (fakes; no I/O)."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.admin_support.adm01_postgres_issuance_read_adapter import Adm01PostgresIssuanceReadAdapter
from app.admin_support.contracts import IssuanceOperationalState, IssuanceOperationalSummary
from app.persistence.issuance_state_record import IssuanceStatePersistence, IssuanceStateRow
from app.security.errors import InternalErrorCategory, PersistenceDependencyError

_TS = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

_REF_SHOULD_NEVER_LEAK = "issuance-ref:fake:super-secret-suffix-abc12345"


def _row(
    state: IssuanceStatePersistence,
    *,
    user: str = "u1",
) -> IssuanceStateRow:
    return IssuanceStateRow(
        internal_user_id=user,
        issue_idempotency_key="ik-1",
        state=state,
        provider_issuance_ref=_REF_SHOULD_NEVER_LEAK,
        created_at=_TS,
        updated_at=_TS,
        revoked_at=_TS if state is IssuanceStatePersistence.REVOKED else None,
    )


def test_summary_exposes_only_state_field() -> None:
    s = IssuanceOperationalSummary(state=IssuanceOperationalState.OK)
    names = {f.name for f in fields(s)}
    assert names == {"state"}


class _FakeRepo:
    def __init__(self, current: object | None) -> None:
        self._current = current
        self.last_user: str | None = None

    async def get_current_for_user(self, internal_user_id: str) -> IssuanceStateRow | None:
        self.last_user = internal_user_id
        return self._current  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_no_row_is_none() -> None:
    a = Adm01PostgresIssuanceReadAdapter(_FakeRepo(None))
    s = await a.get_issuance_summary("u-1")
    assert s.state is IssuanceOperationalState.NONE
    assert _REF_SHOULD_NEVER_LEAK not in repr(s) + str(s)


@pytest.mark.asyncio
async def test_issued_is_ok() -> None:
    a = Adm01PostgresIssuanceReadAdapter(_FakeRepo(_row(IssuanceStatePersistence.ISSUED)))
    s = await a.get_issuance_summary("u-1")
    assert s.state is IssuanceOperationalState.OK
    assert _REF_SHOULD_NEVER_LEAK not in repr(s) + str(s)


@pytest.mark.asyncio
async def test_revoked_is_none_operational() -> None:
    a = Adm01PostgresIssuanceReadAdapter(_FakeRepo(_row(IssuanceStatePersistence.REVOKED)))
    s = await a.get_issuance_summary("u-1")
    assert s.state is IssuanceOperationalState.NONE
    assert _REF_SHOULD_NEVER_LEAK not in repr(s)


class _StrangeState:
    pass


class _UnknownStateRepo:
    """Row-like object with .state that is not issued/revoked."""

    def __init__(self) -> None:
        self.last = ""

    async def get_current_for_user(self, internal_user_id: str) -> object:
        self.last = internal_user_id
        return SimpleNamespace(
            state=_StrangeState(),
        )


@pytest.mark.asyncio
async def test_unrecognized_state_fail_closed_unknown() -> None:
    a = Adm01PostgresIssuanceReadAdapter(_UnknownStateRepo())
    s = await a.get_issuance_summary("u-1")
    assert s.state is IssuanceOperationalState.UNKNOWN


class _ErrorRepo:
    def __init__(self, err: Exception) -> None:
        self._err = err

    async def get_current_for_user(self, internal_user_id: str) -> object:
        del internal_user_id
        raise self._err


@pytest.mark.asyncio
async def test_persistence_dependency_error_propagates() -> None:
    a = Adm01PostgresIssuanceReadAdapter(
        _ErrorRepo(
            PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT),
        )
    )
    with pytest.raises(PersistenceDependencyError) as e:
        await a.get_issuance_summary("u-1")
    assert e.value.category is InternalErrorCategory.PERSISTENCE_TRANSIENT
