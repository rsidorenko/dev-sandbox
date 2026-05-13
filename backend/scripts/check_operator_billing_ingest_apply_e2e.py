"""Operator e2e smoke: normalized ingest (duplicate replay) -> UC-05 apply -> readiness check."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import asyncpg

from app.application.billing_ingestion_main import (
    async_run_billing_ingest_from_parsed,
    parse_json_to_normalized_billing_input,
)
from app.application.billing_subscription_apply_main import async_run_apply
from app.domain.billing_apply_rules import UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED
from app.persistence.postgres_migrations import MigrationLedgerDriftError, apply_postgres_migrations
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.shared.types import OperationOutcomeCategory, SubscriptionSnapshotState

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"
_REQUIRED_DSN_ENV = "DATABASE_URL"
_BILLING_INGEST_OPT_IN_ENV = "BILLING_NORMALIZED_INGEST_ENABLE"
_BILLING_APPLY_OPT_IN_ENV = "BILLING_SUBSCRIPTION_APPLY_ENABLE"
_TRUTHY_ENV_VALUES = {"1", "true", "yes"}
_PREFIX = "operator-e2e-"
_STDOUT_OK = "operator_billing_ingest_apply_e2e: ok"
_STDERR_FAIL = "operator_billing_ingest_apply_e2e: fail"
_STDERR_FAILED = "operator_billing_ingest_apply_e2e: failed"
_FORBIDDEN_OUTPUT_FRAGMENTS = (
    "DATABASE_URL",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "PRIVATE KEY",
    "schema_version",
)


class _SyntheticIds(NamedTuple):
    uid: str
    fact_ref: str
    ext_event_id: str
    correlation_id: str


def _assert_no_forbidden_output(text: str) -> None:
    upper_text = text.upper()
    for frag in _FORBIDDEN_OUTPUT_FRAGMENTS:
        if frag.upper() in upper_text:
            raise RuntimeError("operator e2e smoke output leak guard failed")


def _print_stdout_safe(line: str) -> None:
    _assert_no_forbidden_output(line)
    print(line, flush=True)


def _print_stderr_safe(line: str) -> None:
    _assert_no_forbidden_output(line)
    print(line, file=sys.stderr, flush=True)


def _required_database_url() -> str:
    dsn = os.environ.get(_REQUIRED_DSN_ENV, "").strip()
    if not dsn:
        raise RuntimeError("missing DATABASE_URL")
    return dsn


def _is_truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY_ENV_VALUES


def _require_billing_operator_opt_ins() -> None:
    ingest_enabled = _is_truthy_env(os.environ.get(_BILLING_INGEST_OPT_IN_ENV))
    apply_enabled = _is_truthy_env(os.environ.get(_BILLING_APPLY_OPT_IN_ENV))
    if ingest_enabled and apply_enabled:
        return
    raise RuntimeError("operator billing opt-ins are required")


def _new_synthetic_ids() -> _SyntheticIds:
    suffix = uuid.uuid4().hex[:12]
    prefix = f"{_PREFIX}{suffix}"
    return _SyntheticIds(
        uid=f"{prefix}-user",
        fact_ref=f"{prefix}-fact",
        ext_event_id=f"{prefix}-event",
        correlation_id=f"{prefix}-corr",
    )


def _normalized_fact_json(ids: _SyntheticIds) -> str:
    now_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    payload = {
        "schema_version": 1,
        "billing_provider_key": "operator_e2e_provider",
        "external_event_id": ids.ext_event_id,
        "event_type": UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED,
        "event_effective_at": now_utc,
        "event_received_at": now_utc,
        "status": "accepted",
        "ingestion_correlation_id": ids.correlation_id,
        "internal_fact_ref": ids.fact_ref,
        "internal_user_id": ids.uid,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


async def _cleanup_synthetic_rows(conn: asyncpg.Connection, ids: _SyntheticIds) -> None:
    await conn.execute(
        "DELETE FROM billing_subscription_apply_audit_events WHERE internal_fact_ref = $1::text",
        ids.fact_ref,
    )
    await conn.execute(
        "DELETE FROM billing_subscription_apply_records WHERE internal_fact_ref = $1::text",
        ids.fact_ref,
    )
    await conn.execute(
        "DELETE FROM billing_ingestion_audit_events WHERE external_event_id = $1::text",
        ids.ext_event_id,
    )
    await conn.execute(
        "DELETE FROM billing_events_ledger WHERE internal_fact_ref = $1::text",
        ids.fact_ref,
    )
    await conn.execute(
        "DELETE FROM subscription_snapshots WHERE internal_user_id = $1::text",
        ids.uid,
    )


async def _assert_subscription_active(pool: asyncpg.Pool, *, internal_user_id: str) -> None:
    snap_reader = PostgresSubscriptionSnapshotReader(pool)
    snapshot = await snap_reader.get_for_user(internal_user_id)
    if snapshot is None:
        raise RuntimeError("missing snapshot after apply")
    if snapshot.state_label != SubscriptionSnapshotState.ACTIVE.value:
        raise RuntimeError("snapshot not active after apply")


async def run_operator_billing_ingest_apply_e2e() -> None:
    _require_billing_operator_opt_ins()
    dsn = _required_database_url()
    ids = _new_synthetic_ids()
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        async with pool.acquire() as conn:
            await _cleanup_synthetic_rows(conn, ids)

        parsed = parse_json_to_normalized_billing_input(_normalized_fact_json(ids))
        ingest_outcome_1, ingest_ref_1, _, _ = await async_run_billing_ingest_from_parsed(parsed, dsn=dsn)
        if ingest_ref_1 != ids.fact_ref:
            raise RuntimeError("ingest returned mismatched internal_fact_ref")
        if ingest_outcome_1 != "accepted":
            raise RuntimeError("first ingest must accept fresh synthetic fact")

        ingest_outcome_2, ingest_ref_2, _, _ = await async_run_billing_ingest_from_parsed(parsed, dsn=dsn)
        if ingest_ref_2 != ids.fact_ref:
            raise RuntimeError("second ingest returned mismatched internal_fact_ref")
        if ingest_outcome_2 != "idempotent_replay":
            raise RuntimeError("second ingest must be idempotent_replay for duplicate normalized fact")

        apply_res = await async_run_apply(ids.fact_ref, dsn=dsn)
        if apply_res.operation_outcome not in (
            OperationOutcomeCategory.SUCCESS,
            OperationOutcomeCategory.IDEMPOTENT_NOOP,
        ):
            raise RuntimeError("apply did not produce success or idempotent_noop")

        await _assert_subscription_active(pool, internal_user_id=ids.uid)

        apply_res_second = await async_run_apply(ids.fact_ref, dsn=dsn)
        if apply_res_second.operation_outcome is not OperationOutcomeCategory.IDEMPOTENT_NOOP:
            raise RuntimeError("second apply must be idempotent_noop")
    finally:
        try:
            async with pool.acquire() as conn:
                await _cleanup_synthetic_rows(conn, ids)
        finally:
            await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        asyncio.run(run_operator_billing_ingest_apply_e2e())
    except MigrationLedgerDriftError:
        _print_stderr_safe("operator_billing_ingest_apply_e2e: fail_ledger_drift")
        return 1
    except RuntimeError:
        _print_stderr_safe(_STDERR_FAIL)
        return 1
    except Exception:
        _print_stderr_safe(_STDERR_FAILED)
        return 1
    _print_stdout_safe(_STDOUT_OK)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
