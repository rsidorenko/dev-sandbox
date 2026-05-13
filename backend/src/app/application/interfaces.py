"""Persistence and audit contracts for slice 1 (Protocols only; no concrete DB)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.security.errors import InternalErrorCategory
from app.shared.types import OperationOutcomeCategory


@dataclass(frozen=True, slots=True)
class IdentityRecord:
    """Internal user identity bound to an external Telegram user."""

    internal_user_id: str
    telegram_user_id: int


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Stored idempotency outcome reference (opaque to domain)."""

    key: str
    completed: bool


@dataclass(frozen=True, slots=True)
class SubscriptionSnapshot:
    """Read model input for UC-02 (technology-agnostic)."""

    internal_user_id: str
    state_label: str
    active_until_utc: datetime | None = None
    plan_id: str | None = None
    device_count: int | None = None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Minimal technical audit payload for UC-01 (no PII, no raw payloads)."""

    correlation_id: str
    operation: str
    outcome: OperationOutcomeCategory
    internal_category: InternalErrorCategory | None


class UserIdentityRepository(Protocol):
    async def find_by_telegram_user_id(self, telegram_user_id: int) -> IdentityRecord | None: ...

    async def create_if_absent(self, telegram_user_id: int) -> IdentityRecord: ...


class IdempotencyRepository(Protocol):
    async def get(self, key: str) -> IdempotencyRecord | None: ...

    async def begin_or_get(self, key: str) -> IdempotencyRecord: ...

    async def complete(self, key: str) -> None:
        """Mark the idempotency key as successfully processed (UC-01 commit point)."""
        ...


class SubscriptionSnapshotReader(Protocol):
    async def get_for_user(self, internal_user_id: str) -> SubscriptionSnapshot | None: ...


class SubscriptionSnapshotWriter(Protocol):
    """Insert-if-missing for UC-01; UC-05 may update state via :meth:`upsert_state`."""

    async def put_if_absent(self, snapshot: SubscriptionSnapshot) -> None: ...

    async def upsert_state(self, snapshot: SubscriptionSnapshot) -> None:
        """Set subscription snapshot for user (insert or update ``state_label``)."""
        ...


class AuditAppender(Protocol):
    async def append(self, event: AuditEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class OutboundDeliveryRecord:
    """UC-01 Telegram delivery state keyed by the same idempotency key as bootstrap (no message body)."""

    status: Literal["pending", "sent"]
    telegram_message_id: int | None


class OutboundDeliveryLedger(Protocol):
    """Outbound send durability for UC-01 identity_ready (pending until Telegram confirms message_id)."""

    async def ensure_pending(self, idempotency_key: str) -> None:
        """Create a pending row if none exists; never downgrade ``sent``."""

        ...

    async def get_status(self, idempotency_key: str) -> OutboundDeliveryRecord | None:
        """Return current row or ``None`` if the ledger has no record for the key."""

        ...

    async def mark_sent(self, idempotency_key: str, telegram_message_id: int) -> None:
        """Idempotently mark ``pending`` as ``sent`` with a Telegram ``message_id``."""

        ...
