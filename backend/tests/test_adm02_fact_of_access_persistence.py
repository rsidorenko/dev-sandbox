from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.admin_support.contracts import Adm02FactOfAccessDisclosureCategory, AdminActorRef
from app.persistence.adm02_fact_of_access import (
    Adm02FactOfAccessAppendRecord,
    InMemoryAdm02FactOfAccessRecordAppender,
)


def _run(coro):
    return asyncio.run(coro)


def test_adm02_append_record_construction_shape() -> None:
    occurred_at = datetime(2026, 4, 16, 12, 0, tzinfo=UTC)
    record = Adm02FactOfAccessAppendRecord(
        occurred_at=occurred_at,
        correlation_id="cid-1",
        actor_ref=AdminActorRef(internal_admin_principal_id="adm-1"),
        capability_class="adm02_billing_quarantine_reconciliation_diagnostics",
        internal_user_scope_ref="u-1",
        disclosure=Adm02FactOfAccessDisclosureCategory.PARTIAL,
    )

    assert record.occurred_at is occurred_at
    assert record.correlation_id == "cid-1"
    assert record.actor_ref.internal_admin_principal_id == "adm-1"
    assert record.capability_class == "adm02_billing_quarantine_reconciliation_diagnostics"
    assert record.internal_user_scope_ref == "u-1"
    assert record.disclosure is Adm02FactOfAccessDisclosureCategory.PARTIAL


def test_in_memory_adm02_appender_is_append_only() -> None:
    async def main() -> None:
        appender = InMemoryAdm02FactOfAccessRecordAppender()
        first = Adm02FactOfAccessAppendRecord(
            occurred_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            correlation_id="cid-1",
            actor_ref=AdminActorRef(internal_admin_principal_id="adm-1"),
            capability_class="adm02_billing_quarantine_reconciliation_diagnostics",
            internal_user_scope_ref="u-1",
            disclosure=Adm02FactOfAccessDisclosureCategory.UNREDACTED,
        )
        second = Adm02FactOfAccessAppendRecord(
            occurred_at=datetime(2026, 4, 16, 12, 1, tzinfo=UTC),
            correlation_id="cid-2",
            actor_ref=AdminActorRef(internal_admin_principal_id="adm-2"),
            capability_class="adm02_billing_quarantine_reconciliation_diagnostics",
            internal_user_scope_ref="u-1",
            disclosure=Adm02FactOfAccessDisclosureCategory.FULLY_REDACTED,
        )

        await appender.append(first)
        await appender.append(second)

        recorded = await appender.recorded_for_tests()

        assert recorded == (first, second)

    _run(main())


def test_in_memory_adm02_appender_readback_isolated_from_mutated_source_reference() -> None:
    async def main() -> None:
        appender = InMemoryAdm02FactOfAccessRecordAppender()
        actor = AdminActorRef(internal_admin_principal_id="adm-1")
        record = Adm02FactOfAccessAppendRecord(
            occurred_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            correlation_id="cid-1",
            actor_ref=actor,
            capability_class="adm02_billing_quarantine_reconciliation_diagnostics",
            internal_user_scope_ref="u-1",
            disclosure=Adm02FactOfAccessDisclosureCategory.UNREDACTED,
        )

        await appender.append(record)
        recorded = await appender.recorded_for_tests()

        assert recorded[0] is not record
        assert recorded[0].actor_ref is not actor
        assert recorded[0].actor_ref.internal_admin_principal_id == "adm-1"

    _run(main())
