"""Unit tests for slice-1 manual retention cleanup (no real database)."""

from __future__ import annotations

import re
from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

from app.persistence.slice1_retention_manual_cleanup import (
    ENV_BATCH,
    ENV_MAX_ROUNDS,
    ENV_TTL,
    RetentionCleanupResult,
    RetentionSettings,
    retention_cutoff,
    run_slice1_retention_cleanup,
    validate_retention_settings,
)
from app.security.config import ConfigurationError


def _settings(
    *,
    ttl: int = 3600,
    batch: int = 100,
    dry: bool = False,
    max_rounds: int = 10,
) -> RetentionSettings:
    return RetentionSettings(
        ttl_seconds=ttl,
        batch_limit=batch,
        dry_run=dry,
        max_rounds=max_rounds,
    )


@pytest.mark.parametrize(
    ("settings", "needle"),
    [
        (_settings(ttl=0), ENV_TTL),
        (_settings(ttl=-1), ENV_TTL),
        (_settings(batch=0), ENV_BATCH),
        (_settings(batch=-3), ENV_BATCH),
        (_settings(max_rounds=0), ENV_MAX_ROUNDS),
        (_settings(max_rounds=-1), ENV_MAX_ROUNDS),
    ],
)
def test_validate_retention_settings_rejects_non_positive(
    settings: RetentionSettings,
    needle: str,
) -> None:
    with pytest.raises(ConfigurationError) as exc:
        validate_retention_settings(settings)
    assert needle in str(exc.value)


class _FakeSql:
    def __init__(self) -> None:
        self.fetchval_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []
        self._fetchval_queue: list[int] = []
        self._execute_queue: list[str] = []

    def enqueue_fetchval(self, *values: int) -> None:
        self._fetchval_queue.extend(values)

    def enqueue_execute(self, *statuses: str) -> None:
        self._execute_queue.extend(statuses)

    async def fetchval(self, query: str, *args: object) -> object:
        self.fetchval_calls.append((query, args))
        return self._fetchval_queue.pop(0)

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return self._execute_queue.pop(0)


@pytest.mark.asyncio
async def test_dry_run_uses_count_only_no_delete() -> None:
    sql = _FakeSql()
    sql.enqueue_fetchval(12, 3, 7)
    now = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
    result = await run_slice1_retention_cleanup(
        sql,
        now_utc=now,
        settings=_settings(dry=True),
    )
    assert result.dry_run is True
    assert result.audit_rows == 12
    assert result.idempotency_rows == 3
    assert result.outbound_delivery_rows_matched == 7
    assert result.outbound_delivery_rows_deleted == 0
    assert result.rounds == 0
    assert len(sql.fetchval_calls) == 3
    assert len(sql.execute_calls) == 0
    for q, _ in sql.fetchval_calls:
        assert "COUNT(*)" in q
        assert "DELETE" not in q


@pytest.mark.asyncio
async def test_delete_path_aggregates_until_both_zero() -> None:
    sql = _FakeSql()
    sql.enqueue_execute("DELETE 5", "DELETE 2", "DELETE 1")
    sql.enqueue_execute("DELETE 1", "DELETE 0", "DELETE 0")
    sql.enqueue_execute("DELETE 0", "DELETE 0", "DELETE 0")
    now = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
    result = await run_slice1_retention_cleanup(
        sql,
        now_utc=now,
        settings=_settings(dry=False, batch=50, max_rounds=20),
    )
    assert result.dry_run is False
    assert result.audit_rows == 6
    assert result.idempotency_rows == 2
    assert result.outbound_delivery_rows_matched == 0
    assert result.outbound_delivery_rows_deleted == 1
    assert result.rounds == 3
    assert len(sql.execute_calls) == 9


@pytest.mark.asyncio
async def test_delete_path_passes_same_cutoff_and_batch_to_both_tables() -> None:
    sql = _FakeSql()
    sql.enqueue_execute("DELETE 0", "DELETE 0", "DELETE 0")
    now = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
    expected_cutoff = retention_cutoff(now_utc=now, ttl_seconds=3600)
    result = await run_slice1_retention_cleanup(
        sql,
        now_utc=now,
        settings=_settings(dry=False, batch=17, max_rounds=1),
    )
    assert len(sql.execute_calls) == 3
    q_audit, args_audit = sql.execute_calls[0]
    q_idem, args_idem = sql.execute_calls[1]
    q_out, args_out = sql.execute_calls[2]
    assert "slice1_audit_events" in q_audit
    assert "idempotency_records" in q_idem
    assert "slice1_uc01_outbound_deliveries" in q_out
    assert args_audit[0] == args_idem[0] == args_out[0] == expected_cutoff
    assert args_audit[0].tzinfo == UTC
    assert args_audit[1] == args_idem[1] == args_out[1] == 17
    assert result.rounds == 1


@pytest.mark.asyncio
async def test_delete_respects_max_rounds() -> None:
    sql = _FakeSql()
    for _ in range(5):
        sql.enqueue_execute("DELETE 1", "DELETE 1", "DELETE 0")
    now = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
    result = await run_slice1_retention_cleanup(
        sql,
        now_utc=now,
        settings=_settings(dry=False, max_rounds=2),
    )
    assert result.rounds == 2
    assert result.audit_rows == 2
    assert result.idempotency_rows == 2
    assert result.outbound_delivery_rows_deleted == 0
    assert len(sql.execute_calls) == 6


def test_idempotency_sql_guardrail_completed_true() -> None:
    from app.persistence import slice1_retention_manual_cleanup as m

    assert "completed = true" in m._COUNT_IDEMPOTENCY
    assert "completed = true" in m._DELETE_IDEMPOTENCY_BATCH
    assert "completed = false" not in m._DELETE_IDEMPOTENCY_BATCH.lower()


def test_outbound_delivery_sql_guardrail_sent_only() -> None:
    from app.persistence import slice1_retention_manual_cleanup as m

    assert "delivery_status = 'sent'" in m._COUNT_OUTBOUND_DELIVERY
    assert "delivery_status = 'sent'" in m._DELETE_OUTBOUND_DELIVERY_BATCH
    assert "delivery_status = 'pending'" not in m._DELETE_OUTBOUND_DELIVERY_BATCH.lower()


@pytest.mark.asyncio
async def test_idempotency_count_and_delete_predicates() -> None:
    sql = _FakeSql()
    sql.enqueue_fetchval(0, 0, 0)
    now = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
    await run_slice1_retention_cleanup(sql, now_utc=now, settings=_settings(dry=True))
    count_q = " ".join(q for q, _ in sql.fetchval_calls)
    assert "completed = true" in count_q
    assert "completed = false" not in count_q.lower()


@pytest.mark.asyncio
async def test_result_summary_has_no_dsn_or_key_lists() -> None:
    sql = _FakeSql()
    sql.enqueue_fetchval(1, 1, 0)
    now = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
    result = await run_slice1_retention_cleanup(
        sql,
        now_utc=now,
        settings=_settings(dry=True),
    )
    blob = (
        f"{result.cutoff_iso!s} {result.audit_rows} {result.idempotency_rows} "
        f"{result.outbound_delivery_rows_matched} {result.outbound_delivery_rows_deleted}"
    )
    assert not re.search(r"postgres(ql)?://", blob, re.I)
    assert "correlation" not in blob.lower()
    assert "idempotency_key" not in blob.lower()


def test_retention_cleanup_result_is_low_cardinality_fields_only() -> None:
    RetentionCleanupResult(
        dry_run=False,
        cutoff_iso="2026-04-24T00:00:00+00:00",
        audit_rows=1,
        idempotency_rows=2,
        outbound_delivery_rows_matched=0,
        outbound_delivery_rows_deleted=3,
        rounds=3,
    )
    names = {f.name for f in fields(RetentionCleanupResult)}
    assert names == {
        "dry_run",
        "cutoff_iso",
        "audit_rows",
        "idempotency_rows",
        "outbound_delivery_rows_matched",
        "outbound_delivery_rows_deleted",
        "rounds",
    }


def test_cutoff_moves_back_by_ttl() -> None:
    from app.persistence.slice1_retention_manual_cleanup import retention_cutoff

    now = datetime(2026, 4, 24, 15, 0, tzinfo=UTC)
    c = retention_cutoff(now_utc=now, ttl_seconds=3600)
    assert c == now - timedelta(seconds=3600)
