"""Tests for ADM-01 identity resolve adapter."""

from __future__ import annotations

import asyncio

from app.admin_support.adm01_identity_resolve_adapter import Adm01IdentityResolveAdapter
from app.admin_support.contracts import InternalUserTarget, TelegramUserTarget
from app.application.interfaces import IdentityRecord


def _run(coro):
    return asyncio.run(coro)


class _IdentityRepo:
    def __init__(self, record: IdentityRecord | None) -> None:
        self._record = record

    async def find_by_telegram_user_id(self, telegram_user_id: int):
        del telegram_user_id
        return self._record


def test_internal_target_passes_through() -> None:
    async def main() -> None:
        adapter = Adm01IdentityResolveAdapter(_IdentityRepo(None))
        uid = await adapter.resolve_internal_user_id(
            InternalUserTarget(internal_user_id="u-1"),
            correlation_id="cid",
        )
        assert uid == "u-1"

    _run(main())


def test_telegram_target_resolves_from_repository() -> None:
    async def main() -> None:
        adapter = Adm01IdentityResolveAdapter(_IdentityRepo(IdentityRecord(internal_user_id="u-7", telegram_user_id=7)))
        uid = await adapter.resolve_internal_user_id(
            TelegramUserTarget(telegram_user_id=7),
            correlation_id="cid",
        )
        assert uid == "u-7"

    _run(main())


def test_telegram_target_missing_identity_returns_none() -> None:
    async def main() -> None:
        adapter = Adm01IdentityResolveAdapter(_IdentityRepo(None))
        uid = await adapter.resolve_internal_user_id(
            TelegramUserTarget(telegram_user_id=404),
            correlation_id="cid",
        )
        assert uid is None

    _run(main())
