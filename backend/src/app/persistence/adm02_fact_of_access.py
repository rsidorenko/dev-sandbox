"""ADM-02 fact-of-access append-only persistence primitives."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.admin_support.contracts import Adm02FactOfAccessDisclosureCategory, AdminActorRef


@dataclass(frozen=True, slots=True)
class Adm02FactOfAccessAppendRecord:
    """Minimal persisted fact-of-access record for ADM-02 append-only audit."""

    occurred_at: datetime
    correlation_id: str
    actor_ref: AdminActorRef
    capability_class: str
    internal_user_scope_ref: str
    disclosure: Adm02FactOfAccessDisclosureCategory


class Adm02FactOfAccessRecordAppender(Protocol):
    """Append-only storage contract for ADM-02 fact-of-access records."""

    async def append(self, record: Adm02FactOfAccessAppendRecord) -> None: ...


class InMemoryAdm02FactOfAccessRecordAppender:
    """Append-only in-memory test double with readback for tests only."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: list[Adm02FactOfAccessAppendRecord] = []

    async def append(self, record: Adm02FactOfAccessAppendRecord) -> None:
        async with self._lock:
            self._records.append(
                Adm02FactOfAccessAppendRecord(
                    occurred_at=record.occurred_at,
                    correlation_id=record.correlation_id,
                    actor_ref=AdminActorRef(internal_admin_principal_id=record.actor_ref.internal_admin_principal_id),
                    capability_class=record.capability_class,
                    internal_user_scope_ref=record.internal_user_scope_ref,
                    disclosure=record.disclosure,
                )
            )

    async def recorded_for_tests(self) -> tuple[Adm02FactOfAccessAppendRecord, ...]:
        async with self._lock:
            return tuple(self._records)
