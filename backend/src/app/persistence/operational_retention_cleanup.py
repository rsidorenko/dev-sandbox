"""Operational retention cleanup for durable Telegram dedup and ADM-02 audit tables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

ENV_OPERATIONAL_RETENTION_DELETE_ENABLE = "OPERATIONAL_RETENTION_DELETE_ENABLE"
ENV_ADM02_AUDIT_RETENTION_DAYS = "ADM02_AUDIT_RETENTION_DAYS"

_COUNT_TELEGRAM_DEDUP_EXPIRED = """
    SELECT COUNT(*)::bigint
    FROM telegram_update_dedup
    WHERE expires_at <= $1::timestamptz
"""

_DELETE_TELEGRAM_DEDUP_EXPIRED = """
    DELETE FROM telegram_update_dedup
    WHERE expires_at <= $1::timestamptz
"""

_COUNT_ADM02_AUDIT_EXPIRED = """
    SELECT COUNT(*)::bigint
    FROM adm02_ensure_access_audit_events
    WHERE created_at < $1::timestamptz
"""

_DELETE_ADM02_AUDIT_EXPIRED = """
    DELETE FROM adm02_ensure_access_audit_events
    WHERE created_at < $1::timestamptz
"""


@dataclass(frozen=True, slots=True)
class OperationalRetentionSettings:
    dry_run: bool
    adm02_audit_retention_days: int


@dataclass(frozen=True, slots=True)
class OperationalRetentionResult:
    dry_run: bool
    telegram_update_dedup_expired_rows: int
    telegram_update_dedup_deleted_rows: int
    adm02_audit_expired_rows: int
    adm02_audit_deleted_rows: int
    adm02_audit_retention_days: int


@runtime_checkable
class OperationalRetentionSqlConnection(Protocol):
    async def fetchval(self, query: str, *args: object) -> object: ...

    async def execute(self, query: str, *args: object) -> str: ...


def _parse_delete_count(status: str) -> int:
    if not status.startswith("DELETE "):
        return 0
    tail = status.split(maxsplit=1)[1].strip()
    try:
        return int(tail)
    except ValueError:
        return 0


def _audit_cutoff(now_utc: datetime, retention_days: int) -> datetime:
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    return now_utc.astimezone(UTC) - timedelta(days=retention_days)


async def run_operational_retention_cleanup(
    sql: OperationalRetentionSqlConnection,
    *,
    now_utc: datetime,
    settings: OperationalRetentionSettings,
) -> OperationalRetentionResult:
    cutoff = _audit_cutoff(now_utc, settings.adm02_audit_retention_days)
    if settings.dry_run:
        raw_dedup = await sql.fetchval(_COUNT_TELEGRAM_DEDUP_EXPIRED, now_utc.astimezone(UTC))
        raw_audit = await sql.fetchval(_COUNT_ADM02_AUDIT_EXPIRED, cutoff)
        return OperationalRetentionResult(
            dry_run=True,
            telegram_update_dedup_expired_rows=int(raw_dedup or 0),
            telegram_update_dedup_deleted_rows=0,
            adm02_audit_expired_rows=int(raw_audit or 0),
            adm02_audit_deleted_rows=0,
            adm02_audit_retention_days=settings.adm02_audit_retention_days,
        )

    dedup_deleted = _parse_delete_count(await sql.execute(_DELETE_TELEGRAM_DEDUP_EXPIRED, now_utc.astimezone(UTC)))
    audit_deleted = _parse_delete_count(await sql.execute(_DELETE_ADM02_AUDIT_EXPIRED, cutoff))
    return OperationalRetentionResult(
        dry_run=False,
        telegram_update_dedup_expired_rows=dedup_deleted,
        telegram_update_dedup_deleted_rows=dedup_deleted,
        adm02_audit_expired_rows=audit_deleted,
        adm02_audit_deleted_rows=audit_deleted,
        adm02_audit_retention_days=settings.adm02_audit_retention_days,
    )
