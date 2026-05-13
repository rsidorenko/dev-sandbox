"""ADM-01 issuance read: durable operational summary from `issuance_state` (no provider refs in output)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.admin_support.contracts import Adm01IssuanceReadPort, IssuanceOperationalState, IssuanceOperationalSummary
from app.persistence.issuance_state_record import IssuanceStatePersistence, IssuanceStateRow


# get_current is ordered by `updated_at`; latest row may be `revoked`. For admin: no active issuance
# in `IssuanceOperationalState` (no `revoked` value) -> conservative mapping to NONE, not OK.
@runtime_checkable
class _IssuanceCurrentForUser(Protocol):
    """Tests may inject fakes; production uses :class:`PostgresIssuanceStateRepository`."""

    async def get_current_for_user(self, internal_user_id: str) -> IssuanceStateRow | None: ...


class Adm01PostgresIssuanceReadAdapter(Adm01IssuanceReadPort):
    """
    Maps persisted issuance rows to a low-cardinality :class:`IssuanceOperationalSummary`.

    * Does not return or log ``provider_issuance_ref`` or any secret-bearing material.
    * ``PersistenceDependencyError`` from the repository is propagated; :class:`Adm01LookupHandler` maps
      that to ``DEPENDENCY_FAILURE``.
    """

    def __init__(self, issuance_state: _IssuanceCurrentForUser) -> None:
        self._issuance_state = issuance_state

    async def get_issuance_summary(self, internal_user_id: str) -> IssuanceOperationalSummary:
        row = await self._issuance_state.get_current_for_user(internal_user_id)
        if row is None:
            return IssuanceOperationalSummary(state=IssuanceOperationalState.NONE)

        st = row.state
        if st is IssuanceStatePersistence.ISSUED:
            return IssuanceOperationalSummary(state=IssuanceOperationalState.OK)
        if st is IssuanceStatePersistence.REVOKED:
            return IssuanceOperationalSummary(state=IssuanceOperationalState.NONE)

        return IssuanceOperationalSummary(state=IssuanceOperationalState.UNKNOWN)
