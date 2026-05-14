from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.admin_support.adm02_fact_of_access_audit_adapter import (
    Adm02FactOfAccessPersistenceAuditAdapter,
)
from app.admin_support.contracts import (
    Adm02FactOfAccessAuditRecord,
    Adm02FactOfAccessDisclosureCategory,
    AdminActorRef,
)
from app.persistence.adm02_fact_of_access import InMemoryAdm02FactOfAccessRecordAppender
from app.shared.test_helpers import run_async as _run


def _audit_record() -> Adm02FactOfAccessAuditRecord:
    return Adm02FactOfAccessAuditRecord(
        actor=AdminActorRef(internal_admin_principal_id="adm-1"),
        capability_class="adm02_billing_quarantine_reconciliation_diagnostics",
        internal_user_scope_ref="u-1",
        correlation_id="cid-1",
        disclosure=Adm02FactOfAccessDisclosureCategory.PARTIAL,
    )


def test_adm02_adapter_maps_all_fields_and_uses_injected_now_provider() -> None:
    async def main() -> None:
        persisted = InMemoryAdm02FactOfAccessRecordAppender()
        expected_now = datetime(2026, 4, 16, 12, 34, 56, tzinfo=UTC)

        adapter = Adm02FactOfAccessPersistenceAuditAdapter(
            appender=persisted,
            now_provider=lambda: expected_now,
        )

        await adapter.append_fact_of_access(_audit_record())

        recorded = await persisted.recorded_for_tests()
        assert len(recorded) == 1
        rec = recorded[0]

        assert rec.occurred_at is expected_now
        assert rec.correlation_id == "cid-1"
        assert rec.actor_ref.internal_admin_principal_id == "adm-1"
        assert rec.capability_class == "adm02_billing_quarantine_reconciliation_diagnostics"
        assert rec.internal_user_scope_ref == "u-1"
        assert rec.disclosure is Adm02FactOfAccessDisclosureCategory.PARTIAL

    _run(main())


def test_adm02_adapter_writes_to_inmemory_appender() -> None:
    async def main() -> None:
        persisted = InMemoryAdm02FactOfAccessRecordAppender()
        adapter = Adm02FactOfAccessPersistenceAuditAdapter(
            appender=persisted,
            now_provider=lambda: datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        )

        await adapter.append_fact_of_access(_audit_record())

        recorded = await persisted.recorded_for_tests()
        assert len(recorded) == 1

    _run(main())


def test_adm02_adapter_does_not_swallow_persistence_exceptions() -> None:
    class _FailingAppender:
        async def append(self, record) -> None:
            raise RuntimeError("append failed")

    async def main() -> None:
        adapter = Adm02FactOfAccessPersistenceAuditAdapter(
            appender=_FailingAppender(),
            now_provider=lambda: datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        )

        with pytest.raises(RuntimeError, match="append failed"):
            await adapter.append_fact_of_access(_audit_record())

    _run(main())
