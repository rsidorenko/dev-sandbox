"""PostgreSQL: one transaction for UC-05 ledger read + idempotency + snapshot + apply audit."""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from app.application.interfaces import SubscriptionSnapshot
from app.domain.uc05_apply_decision import first_time_decision
from app.persistence.billing_subscription_apply_contracts import (
    BillingSubscriptionApplyAuditRecord,
    BillingSubscriptionApplyOutcome,
)
from app.persistence.postgres_billing_events_ledger import PostgresBillingEventsLedgerRepository
from app.persistence.postgres_billing_subscription_apply_audit import (
    PostgresBillingSubscriptionApplyAuditAppender,
)
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.security.errors import InternalErrorCategory, PersistenceDependencyError
from app.security.validation import ValidationError, validate_internal_fact_ref_uc05
from app.shared.types import OperationOutcomeCategory


@dataclass(frozen=True, slots=True)
class UC05PostgresApplyResult:
    """Outcome of :meth:`PostgresAtomicUC05SubscriptionApply.apply_by_internal_fact_ref`."""

    operation_outcome: OperationOutcomeCategory
    idempotent_replay: bool
    apply_outcome: BillingSubscriptionApplyOutcome | None


class PostgresAtomicUC05SubscriptionApply:
    """One pool connection, one transaction: UC-05 durable apply."""

    _SELECT_APPLY = """
        SELECT apply_outcome
        FROM billing_subscription_apply_records
        WHERE internal_fact_ref = $1::text
    """

    _INSERT_APPLY = """
        INSERT INTO billing_subscription_apply_records (internal_fact_ref, internal_user_id, apply_outcome)
        VALUES ($1::text, $2::text, $3::text)
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def apply_by_internal_fact_ref(
        self,
        internal_fact_ref: str,
    ) -> UC05PostgresApplyResult:
        try:
            validate_internal_fact_ref_uc05(internal_fact_ref)
        except ValidationError:
            return UC05PostgresApplyResult(
                operation_outcome=OperationOutcomeCategory.VALIDATION_FAILURE,
                idempotent_replay=False,
                apply_outcome=None,
            )

        try:
            async with self._pool.acquire() as conn, conn.transaction():
                return await self._apply_in_transaction(conn, internal_fact_ref)
        except (asyncpg.PostgresError, OSError) as exc:
            raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT) from exc

    async def apply_in_connection(
        self,
        conn: asyncpg.Connection,
        internal_fact_ref: str,
    ) -> UC05PostgresApplyResult:
        """Apply using an existing connection (caller manages the transaction)."""
        try:
            validate_internal_fact_ref_uc05(internal_fact_ref)
        except ValidationError:
            return UC05PostgresApplyResult(
                operation_outcome=OperationOutcomeCategory.VALIDATION_FAILURE,
                idempotent_replay=False,
                apply_outcome=None,
            )
        return await self._apply_in_transaction(conn, internal_fact_ref)

    async def _apply_in_transaction(
        self,
        conn: asyncpg.Connection,
        internal_fact_ref: str,
    ) -> UC05PostgresApplyResult:
        fact = await PostgresBillingEventsLedgerRepository.get_by_internal_fact_ref_in_connection(
            conn,
            internal_fact_ref,
        )
        if fact is None:
            return UC05PostgresApplyResult(
                operation_outcome=OperationOutcomeCategory.NOT_FOUND,
                idempotent_replay=False,
                apply_outcome=None,
            )

        row = await conn.fetchrow(self._SELECT_APPLY, internal_fact_ref)
        if row is not None:
            ao = BillingSubscriptionApplyOutcome(str(row["apply_outcome"]))
            return UC05PostgresApplyResult(
                operation_outcome=OperationOutcomeCategory.IDEMPOTENT_NOOP,
                idempotent_replay=True,
                apply_outcome=ao,
            )

        ins = first_time_decision(fact)
        if ins.snapshot_state_label is not None:
            await PostgresSubscriptionSnapshotReader.upsert_state_in_connection(
                conn,
                SubscriptionSnapshot(
                    internal_user_id=ins.record_internal_user_id,
                    state_label=ins.snapshot_state_label,
                ),
            )

        await conn.execute(
            self._INSERT_APPLY,
            ins.internal_fact_ref,
            ins.record_internal_user_id,
            ins.apply_outcome.value,
        )

        audit = BillingSubscriptionApplyAuditRecord(
            internal_fact_ref=ins.internal_fact_ref,
            internal_user_id=ins.audit_internal_user_id,
            billing_provider_key=ins.billing_provider_key,
            external_event_id=ins.external_event_id,
            event_type=ins.event_type,
            billing_event_status=ins.billing_event_status,
            apply_outcome=ins.apply_outcome,
            reason=ins.reason,
        )
        await PostgresBillingSubscriptionApplyAuditAppender.append_in_connection(conn, audit)

        return UC05PostgresApplyResult(
            operation_outcome=OperationOutcomeCategory.SUCCESS,
            idempotent_replay=False,
            apply_outcome=ins.apply_outcome,
        )
