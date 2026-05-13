"""Optional durable operational state for :class:`app.issuance.service.IssuanceService` (e.g. Postgres)."""

from __future__ import annotations

from typing import Protocol

from app.persistence.issuance_state_record import IssuanceStateRow


class IssuanceOperationalStatePort(Protocol):
    """
    Read/write opaque issuance handles and issued/revoked state.

    Implementations (e.g. :class:`PostgresIssuanceStateRepository`) must preserve
    ``issue_or_get`` idempotency (no ref overwrite on conflict) and idempotent ``mark_revoked``.
    """

    async def fetch_by_issue_keys(
        self, *, internal_user_id: str, issue_idempotency_key: str
    ) -> IssuanceStateRow | None:
        """Return the row for the composite key, or ``None`` if absent."""
        ...

    async def issue_or_get(
        self,
        *,
        internal_user_id: str,
        issue_idempotency_key: str,
        provider_issuance_ref: str,
    ) -> IssuanceStateRow:
        """Persist issued state after provider success; never overwrites an existing ref."""
        ...

    async def mark_revoked(self, *, internal_user_id: str, issue_idempotency_key: str) -> IssuanceStateRow | None:
        """Mark revoked at rest; ``None`` if no row exists."""
        ...


__all__ = ["IssuanceOperationalStatePort"]
