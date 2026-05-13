"""Adm01PostgresIssuanceReadAdapter against real PostgreSQL (opt-in via ``DATABASE_URL``)."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, fields
from pathlib import Path

import asyncpg
import pytest

from app.admin_support.adm01_postgres_issuance_read_adapter import Adm01PostgresIssuanceReadAdapter
from app.admin_support.contracts import IssuanceOperationalState
from app.persistence.issuance_state_record import IssuanceStatePersistence
from app.persistence.postgres_issuance_state import PostgresIssuanceStateRepository

BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_PATH = BACKEND_ROOT / "migrations" / "011_issuance_state.sql"
_KEY_PREFIX = "test_pgadmis_"


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL", "").strip() or None


def _apply_011() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL ADM-01 issuance read adapter tests")
    return url


def test_postgres_adm01_issuance_lifecycle_op_summary_no_leak(
    pg_url: str,
) -> None:
    user = f"{_KEY_PREFIX}user-1"
    ikey = f"{_KEY_PREFIX}ik-1"
    # Intentionally verbose opaque ref: must not appear in admin summary (enum-only DTO).
    oref = f"issuance-ref:fake:cursor-leaktest:{_KEY_PREFIX}suffix"[:64]

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            async with pool.acquire() as conn:
                await conn.execute(_apply_011())
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM issuance_state WHERE internal_user_id = $1::text",
                    user,
                )
            repo = PostgresIssuanceStateRepository(pool)
            adapter = Adm01PostgresIssuanceReadAdapter(repo)

            r1 = await repo.issue_or_get(
                internal_user_id=user,
                issue_idempotency_key=ikey,
                provider_issuance_ref=oref,
            )
            assert r1.state is IssuanceStatePersistence.ISSUED

            s_ok = await adapter.get_issuance_summary(user)
            assert s_ok.state is IssuanceOperationalState.OK
            d_ok = asdict(s_ok)
            assert set(d_ok.keys()) == {f.name for f in fields(s_ok)}
            assert oref not in str(d_ok) and oref not in repr(s_ok) + str(s_ok)

            _ = await repo.mark_revoked(internal_user_id=user, issue_idempotency_key=ikey)
            s_done = await adapter.get_issuance_summary(user)
            # Revoked row is "current" by time ordering; map to operational NONE.
            assert s_done.state is IssuanceOperationalState.NONE
            d_done = asdict(s_done)
            assert oref not in str(d_done) and oref not in repr(s_done) + str(s_done)
        finally:
            await pool.close()

    asyncio.run(main())
