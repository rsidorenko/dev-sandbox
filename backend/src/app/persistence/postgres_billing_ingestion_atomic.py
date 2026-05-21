"""Postgres: one transaction for billing_events_ledger + billing_ingestion_audit_events (normalized ingest)."""

from __future__ import annotations

import asyncpg

from app.application.billing_ingestion import (
    IngestNormalizedBillingFactResult,
    NormalizedBillingFactInput,
    build_ledger_record_for_ingest,
)
from app.persistence.billing_ingestion_audit_contracts import (
    BILLING_INGESTION_AUDIT_OPERATION,
    BILLING_INGESTION_OUTCOME_ACCEPTED,
    BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY,
    BillingIngestionAuditRecord,
)
from app.persistence.postgres_billing_events_ledger import PostgresBillingEventsLedgerRepository
from app.persistence.postgres_billing_ingestion_audit import PostgresBillingIngestionAuditAppender
from app.security.errors import InternalErrorCategory, PersistenceDependencyError


class PostgresAtomicBillingIngestion:
    """One pool connection, one transaction: idempotent ledger append + audit append per handle attempt."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ingest_normalized_billing_fact(
        self,
        input_: NormalizedBillingFactInput,
    ) -> IngestNormalizedBillingFactResult:
        constructed = build_ledger_record_for_ingest(input_)
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                return await self._ingest_in_connection(conn, constructed)
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc

    async def ingest_in_connection(
        self,
        conn: asyncpg.Connection,
        input_: NormalizedBillingFactInput,
    ) -> IngestNormalizedBillingFactResult:
        """Ingest using an existing connection (caller manages the transaction)."""
        constructed = build_ledger_record_for_ingest(input_)
        try:
            return await self._ingest_in_connection(conn, constructed)
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc

    async def _ingest_in_connection(
        self,
        conn: asyncpg.Connection,
        constructed,
    ) -> IngestNormalizedBillingFactResult:
        stored, inserted_new = await PostgresBillingEventsLedgerRepository.append_or_get_in_connection(
            conn,
            constructed,
        )
        is_replay = not inserted_new
        audit_outcome = (
            BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY if is_replay else BILLING_INGESTION_OUTCOME_ACCEPTED
        )
        await PostgresBillingIngestionAuditAppender.append_in_connection(
            conn,
            BillingIngestionAuditRecord(
                internal_fact_ref=stored.internal_fact_ref,
                billing_provider_key=stored.billing_provider_key,
                external_event_id=stored.external_event_id,
                ingestion_correlation_id=stored.ingestion_correlation_id,
                operation=BILLING_INGESTION_AUDIT_OPERATION,
                outcome=audit_outcome,
                billing_event_status=stored.status.value,
                is_idempotent_replay=is_replay,
            ),
        )
        return IngestNormalizedBillingFactResult(
            record=stored,
            is_idempotent_replay=is_replay,
        )
