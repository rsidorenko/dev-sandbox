"""Opt-in slice-1 composition flow on PostgreSQL (DATABASE_URL): migrations → UC-01 → UC-02 → audit → replay."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest

from app.application.bootstrap import build_slice1_composition
from app.application.handlers import BootstrapIdentityInput, GetSubscriptionStatusInput
from app.bot_transport.dispatcher import dispatch_slice1_transport
from app.bot_transport.normalized import TransportIncomingEnvelope
from app.bot_transport.presentation import TransportResponseCategory, TransportStatusCode
from app.persistence.postgres_audit import PostgresAuditAppender
from app.persistence.postgres_idempotency import PostgresIdempotencyRepository
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_outbound_delivery import PostgresOutboundDeliveryLedger
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository, internal_user_id_for_telegram
from app.security.idempotency import build_bootstrap_idempotency_key
from app.shared.types import OperationOutcomeCategory, SafeUserStatusCategory, SubscriptionSnapshotState

BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = BACKEND_ROOT / "migrations"

_TG_USER = 8_888_888_800_910
_UPDATE_ID = 9_101
# require_correlation_id: exactly 32 lowercase hex chars
_CORR_BOOT_1 = "a1b2c3d4e5f67890123456789abcdef0"
_CORR_BOOT_2 = "b2c3d4e5f67890123456789abcdef0a1"
_CORR_STATUS = "c3d4e5f67890123456789abcdef0a1b2"
# Second fixture user: UC-02 transport bridge + missing snapshot row (isolated cleanup).
_TG_UC02_TRANSPORT = 8_888_888_800_912
_UPDATE_UC02_TRANSPORT = 9_102
_CORR_BOOT_UC02 = "d4e5f67890123456789abcdef0a1b2c3"
_CORR_STATUS_UC02_A = "e5f67890123456789abcdef0a1b2c3d4"
_CORR_STATUS_UC02_B = "f67890123456789abcdef0a1b2c3d4e5"
_CORR_STATUS_UC02_C = "7890123456789abcdef0a1b2c3d4e5f6"


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping PostgreSQL slice-1 composition integration test")
    return url


async def _cleanup_test_rows(
    conn: asyncpg.Connection,
    *,
    telegram_user_id: int,
    idempotency_key: str,
    correlation_ids: tuple[str, ...],
) -> None:
    internal = internal_user_id_for_telegram(telegram_user_id)
    for cid in correlation_ids:
        await conn.execute("DELETE FROM slice1_audit_events WHERE correlation_id = $1::text", cid)
    await conn.execute("DELETE FROM idempotency_records WHERE idempotency_key = $1::text", idempotency_key)
    await conn.execute(
        "DELETE FROM slice1_uc01_outbound_deliveries WHERE idempotency_key = $1::text",
        idempotency_key,
    )
    await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id = $1::text", internal)
    await conn.execute("DELETE FROM user_identities WHERE telegram_user_id = $1::bigint", telegram_user_id)


def test_postgres_slice1_composition_bootstrap_status_audit_idempotent_replay(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        idem_key = build_bootstrap_idempotency_key(_TG_USER, _UPDATE_ID)
        corr_pair = (_CORR_BOOT_1, _CORR_BOOT_2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            async with pool.acquire() as conn:
                await _cleanup_test_rows(
                    conn, telegram_user_id=_TG_USER, idempotency_key=idem_key, correlation_ids=corr_pair
                )

            composition = build_slice1_composition(
                identity=PostgresUserIdentityRepository(pool),
                idempotency=PostgresIdempotencyRepository(pool),
                snapshots=PostgresSubscriptionSnapshotReader(pool),
                audit=PostgresAuditAppender(pool),
                outbound_delivery=PostgresOutboundDeliveryLedger(pool),
            )

            r1 = await composition.bootstrap.handle(
                BootstrapIdentityInput(
                    telegram_user_id=_TG_USER,
                    telegram_update_id=_UPDATE_ID,
                    correlation_id=_CORR_BOOT_1,
                ),
            )
            assert r1.outcome == OperationOutcomeCategory.SUCCESS
            assert r1.idempotent_replay is False
            assert r1.internal_user_id == internal_user_id_for_telegram(_TG_USER)
            assert r1.user_safe is None

            snap = await composition.snapshots.get_for_user(r1.internal_user_id or "")
            assert snap is not None
            assert snap.state_label == SubscriptionSnapshotState.INACTIVE.value

            st = await composition.get_status.handle(
                GetSubscriptionStatusInput(telegram_user_id=_TG_USER, correlation_id=_CORR_STATUS),
            )
            assert st.outcome == OperationOutcomeCategory.SUCCESS
            assert st.safe_status == SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE
            assert st.user_safe is None

            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT operation, outcome
                    FROM slice1_audit_events
                    WHERE correlation_id = $1::text
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    _CORR_BOOT_1,
                )
            assert row is not None
            assert row["operation"] == "uc01_bootstrap_identity"
            assert row["outcome"] == OperationOutcomeCategory.SUCCESS.value

            r2 = await composition.bootstrap.handle(
                BootstrapIdentityInput(
                    telegram_user_id=_TG_USER,
                    telegram_update_id=_UPDATE_ID,
                    correlation_id=_CORR_BOOT_2,
                ),
            )
            assert r2.outcome == OperationOutcomeCategory.SUCCESS
            assert r2.idempotent_replay is True
            assert r2.internal_user_id == r1.internal_user_id

            snap2 = await composition.snapshots.get_for_user(r1.internal_user_id or "")
            assert snap2 == snap

            async with pool.acquire() as conn:
                replay_audit = await conn.fetchrow(
                    "SELECT id FROM slice1_audit_events WHERE correlation_id = $1::text",
                    _CORR_BOOT_2,
                )
            assert replay_audit is None
        finally:
            async with pool.acquire() as conn:
                await _cleanup_test_rows(
                    conn, telegram_user_id=_TG_USER, idempotency_key=idem_key, correlation_ids=corr_pair
                )
            await pool.close()

    asyncio.run(main())


def _transport_public_blob(r: object) -> str:
    """Narrow getattr to avoid importing TransportSafeResponse in a circular way."""
    cat = r.category
    code = r.code
    cid = r.correlation_id
    hint = getattr(r, "next_action_hint", None) or ""
    return f"{getattr(cat, 'value', cat)!s}{code!s}{cid!s}{hint!s}"


def _assert_uc02_postgres_transport_blob_has_no_sensitive_leaks(blob_lower: str, *, internal_user_id: str) -> None:
    """Transport-facing blob must not echo persistence wiring, DSN patterns, or internal ids."""
    assert internal_user_id.lower() not in blob_lower
    for needle in (
        "postgresql://",
        "postgres://",
        "internal_user_id",
        "database_url",
    ):
        assert needle not in blob_lower, f"unexpected leakage token: {needle!r}"


def test_postgres_uc02_dispatch_status_transport_and_missing_snapshot_fail_closed(pg_url: str) -> None:
    """Postgres-backed snapshot: /status transport codes + missing row stays fail-closed; no internal id leak."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        idem_key = build_bootstrap_idempotency_key(_TG_UC02_TRANSPORT, _UPDATE_UC02_TRANSPORT)
        corr_all_uc02 = (
            _CORR_BOOT_UC02,
            _CORR_STATUS_UC02_A,
            _CORR_STATUS_UC02_B,
            _CORR_STATUS_UC02_C,
        )
        internal = internal_user_id_for_telegram(_TG_UC02_TRANSPORT)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            async with pool.acquire() as conn:
                await _cleanup_test_rows(
                    conn,
                    telegram_user_id=_TG_UC02_TRANSPORT,
                    idempotency_key=idem_key,
                    correlation_ids=corr_all_uc02,
                )

            composition = build_slice1_composition(
                identity=PostgresUserIdentityRepository(pool),
                idempotency=PostgresIdempotencyRepository(pool),
                snapshots=PostgresSubscriptionSnapshotReader(pool),
                audit=PostgresAuditAppender(pool),
                outbound_delivery=PostgresOutboundDeliveryLedger(pool),
            )

            r_boot = await composition.bootstrap.handle(
                BootstrapIdentityInput(
                    telegram_user_id=_TG_UC02_TRANSPORT,
                    telegram_update_id=_UPDATE_UC02_TRANSPORT,
                    correlation_id=_CORR_BOOT_UC02,
                ),
            )
            assert r_boot.outcome == OperationOutcomeCategory.SUCCESS
            assert r_boot.internal_user_id == internal

            env_status = TransportIncomingEnvelope(
                telegram_user_id=_TG_UC02_TRANSPORT,
                correlation_id=_CORR_STATUS_UC02_A,
                telegram_update_id=None,
                normalized_command_text="/status",
            )
            t1 = await dispatch_slice1_transport(env_status, composition)
            assert t1.category is TransportResponseCategory.SUCCESS
            assert t1.code == TransportStatusCode.INACTIVE_OR_NOT_ELIGIBLE.value
            blob1 = _transport_public_blob(t1).lower()
            _assert_uc02_postgres_transport_blob_has_no_sensitive_leaks(blob1, internal_user_id=internal)

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE subscription_snapshots
                    SET state_label = $2::text
                    WHERE internal_user_id = $1::text
                    """,
                    internal,
                    SubscriptionSnapshotState.NEEDS_REVIEW.value,
                )

            env_needs_review = TransportIncomingEnvelope(
                telegram_user_id=_TG_UC02_TRANSPORT,
                correlation_id=_CORR_STATUS_UC02_C,
                telegram_update_id=None,
                normalized_command_text="/status",
            )
            t_nr = await dispatch_slice1_transport(env_needs_review, composition)
            assert t_nr.category is TransportResponseCategory.SUCCESS
            assert t_nr.code == TransportStatusCode.NEEDS_REVIEW.value
            blob_nr = _transport_public_blob(t_nr).lower()
            _assert_uc02_postgres_transport_blob_has_no_sensitive_leaks(blob_nr, internal_user_id=internal)

            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM subscription_snapshots WHERE internal_user_id = $1::text",
                    internal,
                )

            env_status_b = TransportIncomingEnvelope(
                telegram_user_id=_TG_UC02_TRANSPORT,
                correlation_id=_CORR_STATUS_UC02_B,
                telegram_update_id=None,
                normalized_command_text="/status",
            )
            t2 = await dispatch_slice1_transport(env_status_b, composition)
            assert t2.category is TransportResponseCategory.SUCCESS
            assert t2.code == TransportStatusCode.INACTIVE_OR_NOT_ELIGIBLE.value
            blob2 = _transport_public_blob(t2).lower()
            _assert_uc02_postgres_transport_blob_has_no_sensitive_leaks(blob2, internal_user_id=internal)
        finally:
            async with pool.acquire() as conn:
                await _cleanup_test_rows(
                    conn,
                    telegram_user_id=_TG_UC02_TRANSPORT,
                    idempotency_key=idem_key,
                    correlation_ids=corr_all_uc02,
                )
            await pool.close()

    asyncio.run(main())
