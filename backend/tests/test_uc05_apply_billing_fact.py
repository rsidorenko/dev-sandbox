"""UC-05 apply rules: pure domain + in-memory flow (no PostgreSQL)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.apply_billing_subscription import (
    ApplyAcceptedBillingFactHandler,
    ApplyAcceptedBillingFactInput,
)
from app.application.interfaces import SubscriptionSnapshot
from app.domain.billing_apply_rules import (
    UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED,
    UC05_NO_USER_SENTINEL,
)
from app.domain.uc05_apply_decision import UC05ApplyPath, first_time_decision
from app.persistence.billing_events_ledger_contracts import (
    BillingEventAmountCurrency,
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
)
from app.persistence.billing_events_ledger_in_memory import InMemoryBillingEventsLedgerRepository
from app.persistence.billing_subscription_apply_contracts import BillingSubscriptionApplyOutcome
from app.persistence.in_memory import InMemorySubscriptionSnapshotReader
from app.shared.test_helpers import run_async as _run
from app.shared.types import OperationOutcomeCategory, SubscriptionSnapshotState


def _rec(
    *,
    ref: str = "fact-1",
    status: BillingEventLedgerStatus = BillingEventLedgerStatus.ACCEPTED,
    user: str | None = "u1",
    event_type: str = UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED,
) -> BillingEventLedgerRecord:
    t = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    return BillingEventLedgerRecord(
        internal_fact_ref=ref,
        billing_provider_key="p1",
        external_event_id="ext-1",
        event_type=event_type,
        event_effective_at=t,
        event_received_at=t,
        internal_user_id=user,
        checkout_attempt_id=None,
        amount_currency=BillingEventAmountCurrency(amount_minor_units=1, currency_code="USD"),
        status=status,
        ingestion_correlation_id="c1",
    )


def test_first_time_accepted_allowlisted_sets_active() -> None:
    ins = first_time_decision(_rec())
    assert ins.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED
    assert ins.snapshot_state_label == SubscriptionSnapshotState.ACTIVE.value
    assert ins.record_internal_user_id == "u1"


def test_first_time_not_accepted_no_snapshot() -> None:
    ins = first_time_decision(
        _rec(status=BillingEventLedgerStatus.DUPLICATE),
    )
    assert ins.apply_outcome is BillingSubscriptionApplyOutcome.NO_ACTIVATION
    assert ins.snapshot_state_label is None


def test_first_time_missing_user() -> None:
    ins = first_time_decision(_rec(user=None))
    assert ins.apply_outcome is BillingSubscriptionApplyOutcome.NEEDS_REVIEW
    assert ins.record_internal_user_id == UC05_NO_USER_SENTINEL
    assert ins.snapshot_state_label is None


def test_first_time_unknown_event_needs_review_snapshot() -> None:
    ins = first_time_decision(_rec(event_type="weird_type"))
    assert ins.apply_outcome is BillingSubscriptionApplyOutcome.NEEDS_REVIEW
    assert ins.snapshot_state_label == SubscriptionSnapshotState.NEEDS_REVIEW.value


class InMemoryUc05Engine:
    """Same logical steps as Postgres atomic apply (for unit tests)."""

    def __init__(self) -> None:
        self.ledger = InMemoryBillingEventsLedgerRepository()
        self.snapshots = InMemorySubscriptionSnapshotReader()
        self.apply_outcomes: dict[str, BillingSubscriptionApplyOutcome] = {}
        self.audits: list[str] = []

    async def apply(self, ref: str) -> tuple[UC05ApplyPath, BillingSubscriptionApplyOutcome | None]:
        fact = await self.ledger.get_by_internal_fact_ref(ref)
        if fact is None:
            return UC05ApplyPath.FACT_NOT_FOUND, None
        if ref in self.apply_outcomes:
            return UC05ApplyPath.IDEMPOTENT_REPLAY, self.apply_outcomes[ref]
        ins = first_time_decision(fact)
        if ins.snapshot_state_label is not None:
            await self.snapshots.upsert_state(
                SubscriptionSnapshot(
                    internal_user_id=ins.record_internal_user_id,
                    state_label=ins.snapshot_state_label,
                ),
            )
        self.apply_outcomes[ref] = ins.apply_outcome
        self.audits.append(ref)
        return UC05ApplyPath.PERSIST, ins.apply_outcome


def test_in_memory_idempotent_second_apply_no_extra_audit() -> None:
    async def main() -> None:
        eng = InMemoryUc05Engine()
        r = _rec()
        await eng.ledger.append_or_get_by_provider_and_external_id(r)
        p1, o1 = await eng.apply("fact-1")
        p2, o2 = await eng.apply("fact-1")
        assert p1 is UC05ApplyPath.PERSIST
        assert p2 is UC05ApplyPath.IDEMPOTENT_REPLAY
        assert o1 is o2 is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED
        assert len(eng.audits) == 1

    _run(main())


def test_in_memory_no_raw_payload_in_contracts() -> None:
    """UC-05 audit list stores logical keys only; normalized ledger has no raw blob."""
    r = _rec()
    assert not hasattr(r, "raw")
    d = r.__dataclass_fields__  # type: ignore[attr-defined]
    assert "raw" not in d
    assert "provider_payload" not in d


@pytest.mark.asyncio
async def test_apply_handler_delegates_to_atomic_apply() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.persistence.postgres_billing_subscription_apply import UC05PostgresApplyResult

    apply_pg = MagicMock()
    apply_pg.apply_by_internal_fact_ref = AsyncMock(
        return_value=UC05PostgresApplyResult(
            operation_outcome=OperationOutcomeCategory.SUCCESS,
            idempotent_replay=False,
            apply_outcome=BillingSubscriptionApplyOutcome.ACTIVE_APPLIED,
        ),
    )
    r = await ApplyAcceptedBillingFactHandler(apply_pg).handle(  # type: ignore[arg-type]
        ApplyAcceptedBillingFactInput(internal_fact_ref="fact-a"),
    )
    assert r.operation_outcome is OperationOutcomeCategory.SUCCESS
    assert r.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED
    apply_pg.apply_by_internal_fact_ref.assert_awaited_once_with("fact-a")
