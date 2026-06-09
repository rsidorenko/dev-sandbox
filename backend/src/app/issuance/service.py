"""
Config issuance orchestration: entitlement, idempotency, provider calls.

Process-local idempotency and audit always apply. Optionally, an
:class:`~app.issuance.operational_state.IssuanceOperationalStatePort` (e.g.
:class:`~app.persistence.postgres_issuance_state.PostgresIssuanceStateRepository`)
persists ISSUE / REVOKE operational state across process restarts.

RESEND eligibility hydrates from durable operational state when available.
RESEND call-dedup (``_resend_cached``) remains **process-local only** by design;
durable cross-process resend call-dedup requires a separate schema/slice
(see design doc 33).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.issuance.contracts import (
    CreateAccessOutcome,
    GetSafeInstructionOutcome,
    IssuanceAuditRecord,
    IssuanceOperationType,
    IssuanceOutcomeCategory,
    IssuanceRequest,
    IssuanceServiceResult,
    RevokeAccessOutcome,
)
from app.issuance.entitlement import issue_resend_denial_category, subscription_allows_issue_resend
from app.issuance.operational_state import IssuanceOperationalStatePort
from app.persistence.issuance_state_record import IssuanceStatePersistence, IssuanceStateRow
from app.security.errors import PersistenceDependencyError
from app.shared.correlation import is_valid_correlation_id

if TYPE_CHECKING:
    from app.issuance.contracts import IssuanceProviderPort


class _LedgerState(StrEnum):
    ISSUED = "issued"
    REVOKED = "revoked"


@dataclass
class _LedgerEntry:
    issuance_ref: str
    state: _LedgerState


def _issuance_ledger_key(internal_user_id: str, issue_idempotency_key: str) -> tuple[str, str]:
    return (internal_user_id, issue_idempotency_key)


class IssuanceService:
    """
    Idempotency and category-only audit.

    When ``operational_state`` is set, successful ISSUE and REVOKE outcomes are reflected
    at rest (via the port), and RESEND eligibility can hydrate from durable state.
    RESEND call-dedup remains in-process only (no durable resend cache).
    """

    _MAX_IN_MEMORY_ENTRIES = 10000

    def __init__(
        self,
        provider: IssuanceProviderPort,
        *,
        operational_state: IssuanceOperationalStatePort | None = None,
    ) -> None:
        self._provider = provider
        self._operational_state = operational_state
        self._ledger: dict[tuple[str, str], _LedgerEntry] = {}
        self._issue_idempotent_result: dict[tuple[str, str], IssuanceServiceResult] = {}
        self._revoke_completed: set[tuple[str, str]] = set()
        self._resend_cached: dict[tuple[str, str], str] = {}
        self._audit: list[IssuanceAuditRecord] = []
        self._evict_counter = 0

    @property
    def audit_records(self) -> tuple[IssuanceAuditRecord, ...]:
        return tuple(self._audit)

    def clear_in_memory_state(self) -> None:
        """Test helper: reset process-local ledger and caches (not a production API)."""
        self._ledger.clear()
        self._issue_idempotent_result.clear()
        self._revoke_completed.clear()
        self._resend_cached.clear()
        self._audit.clear()

    def _maybe_evict_caches(self) -> None:
        self._evict_counter += 1
        if self._evict_counter % 100 != 0:
            return
        if len(self._ledger) > self._MAX_IN_MEMORY_ENTRIES:
            keys = list(self._ledger.keys())[: len(self._ledger) // 4]
            for k in keys:
                del self._ledger[k]
        if len(self._issue_idempotent_result) > self._MAX_IN_MEMORY_ENTRIES:
            keys = list(self._issue_idempotent_result.keys())[: len(self._issue_idempotent_result) // 4]
            for k in keys:
                del self._issue_idempotent_result[k]
        if len(self._resend_cached) > self._MAX_IN_MEMORY_ENTRIES:
            keys = list(self._resend_cached.keys())[: len(self._resend_cached) // 4]
            for k in keys:
                del self._resend_cached[k]
        if len(self._audit) > self._MAX_IN_MEMORY_ENTRIES:
            self._audit = self._audit[-self._MAX_IN_MEMORY_ENTRIES // 2:]

    def _append_audit(self, request: IssuanceRequest, category: IssuanceOutcomeCategory) -> None:
        self._maybe_evict_caches()
        self._audit.append(
            IssuanceAuditRecord(
                operation=request.operation,
                outcome=category,
                internal_user_id=request.internal_user_id,
                correlation_id=request.correlation_id,
                idempotency_key=request.idempotency_key,
                link_issue_idempotency_key=request.link_issue_idempotency_key,
            )
        )

    def _validate_basics(self, request: IssuanceRequest) -> IssuanceOutcomeCategory | None:
        if not request.idempotency_key:
            return IssuanceOutcomeCategory.INTERNAL_ERROR
        if not is_valid_correlation_id(request.correlation_id):
            return IssuanceOutcomeCategory.INTERNAL_ERROR
        op = request.operation
        if op is IssuanceOperationType.ISSUE and request.link_issue_idempotency_key is not None:
            return IssuanceOutcomeCategory.INTERNAL_ERROR
        if op is not IssuanceOperationType.ISSUE and not request.link_issue_idempotency_key:
            return IssuanceOutcomeCategory.INTERNAL_ERROR
        return None

    def _get_ledger(self, internal_user_id: str, issue_idempotency_key: str) -> _LedgerEntry | None:
        return self._ledger.get(_issuance_ledger_key(internal_user_id, issue_idempotency_key))

    def _sync_issued_memory(self, lk: tuple[str, str], issuance_ref: str) -> None:
        self._ledger[lk] = _LedgerEntry(issuance_ref=issuance_ref, state=_LedgerState.ISSUED)
        res = IssuanceServiceResult(category=IssuanceOutcomeCategory.ISSUED, safe_ref=issuance_ref)
        self._issue_idempotent_result[lk] = res

    def _sync_from_durable_issued_row(self, lk: tuple[str, str], row: IssuanceStateRow) -> None:
        self._sync_issued_memory(lk, row.provider_issuance_ref)

    def _sync_revoked_memory(self, lk: tuple[str, str], issuance_ref: str) -> None:
        self._ledger[lk] = _LedgerEntry(issuance_ref=issuance_ref, state=_LedgerState.REVOKED)
        if lk in self._issue_idempotent_result:
            del self._issue_idempotent_result[lk]

    def _ok(self, request: IssuanceRequest, result: IssuanceServiceResult) -> IssuanceServiceResult:
        self._append_audit(request, result.category)
        return result

    def _persist_fail(self, request: IssuanceRequest) -> IssuanceServiceResult:
        return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.INTERNAL_ERROR))

    async def execute(self, request: IssuanceRequest) -> IssuanceServiceResult:
        bad = self._validate_basics(request)
        if bad is not None:
            return self._ok(request, IssuanceServiceResult(category=bad, safe_ref=None))

        if request.operation is IssuanceOperationType.ISSUE:
            return await self._execute_issue(request)
        if request.operation is IssuanceOperationType.RESEND:
            return await self._execute_resend(request)
        return await self._execute_revoke(request)

    async def _execute_issue(self, request: IssuanceRequest) -> IssuanceServiceResult:
        u = request.internal_user_id
        key = request.idempotency_key
        lk = _issuance_ledger_key(u, key)

        if not subscription_allows_issue_resend(request.subscription_state):
            cat = issue_resend_denial_category(request.subscription_state)
            return self._ok(request, IssuanceServiceResult(category=cat, safe_ref=None))

        entry = self._ledger.get(lk)
        if entry is not None and entry.state is _LedgerState.REVOKED:
            return self._ok(
                request, IssuanceServiceResult(category=IssuanceOutcomeCategory.INTERNAL_ERROR, safe_ref=None)
            )

        if lk in self._issue_idempotent_result:
            cached = self._issue_idempotent_result[lk]
            return self._ok(
                request,
                IssuanceServiceResult(category=IssuanceOutcomeCategory.ALREADY_ISSUED, safe_ref=cached.safe_ref),
            )

        store = self._operational_state
        if store is not None:
            try:
                row = await store.fetch_by_issue_keys(internal_user_id=u, issue_idempotency_key=key)
            except (PersistenceDependencyError, ValueError):
                return self._persist_fail(request)
            if row is not None:
                if row.state is IssuanceStatePersistence.ISSUED:
                    self._sync_from_durable_issued_row(lk, row)
                    return self._ok(
                        request,
                        IssuanceServiceResult(
                            category=IssuanceOutcomeCategory.ALREADY_ISSUED,
                            safe_ref=row.provider_issuance_ref,
                        ),
                    )
                if row.state is IssuanceStatePersistence.REVOKED:
                    return self._ok(
                        request,
                        IssuanceServiceResult(category=IssuanceOutcomeCategory.INTERNAL_ERROR, safe_ref=None),
                    )

        pr = await self._provider.create_or_ensure_access(
            internal_user_id=u, idempotency_key=key, correlation_id=request.correlation_id
        )
        if pr.outcome is CreateAccessOutcome.SUCCESS:
            if not pr.issuance_ref:
                return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.INTERNAL_ERROR))
            if store is not None:
                try:
                    persisted = await store.issue_or_get(
                        internal_user_id=u,
                        issue_idempotency_key=key,
                        provider_issuance_ref=pr.issuance_ref,
                    )
                except (PersistenceDependencyError, ValueError):
                    return self._persist_fail(request)
                issuance_ref = persisted.provider_issuance_ref
            else:
                issuance_ref = pr.issuance_ref
            self._sync_issued_memory(lk, issuance_ref)
            res = IssuanceServiceResult(category=IssuanceOutcomeCategory.ISSUED, safe_ref=issuance_ref)
            return self._ok(request, res)
        if pr.outcome is CreateAccessOutcome.UNAVAILABLE:
            return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.PROVIDER_UNAVAILABLE))
        if pr.outcome is CreateAccessOutcome.REJECTED:
            return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.PROVIDER_REJECTED))
        return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.INTERNAL_ERROR))

    async def _execute_resend(self, request: IssuanceRequest) -> IssuanceServiceResult:
        u = request.internal_user_id
        assert request.link_issue_idempotency_key is not None
        link = request.link_issue_idempotency_key
        lk = _issuance_ledger_key(u, link)

        if not subscription_allows_issue_resend(request.subscription_state):
            cat = issue_resend_denial_category(request.subscription_state)
            return self._ok(request, IssuanceServiceResult(category=cat, safe_ref=None))

        le = self._get_ledger(u, link)
        store = self._operational_state
        if le is None and store is not None:
            try:
                row = await store.fetch_by_issue_keys(internal_user_id=u, issue_idempotency_key=link)
            except (PersistenceDependencyError, ValueError):
                return self._persist_fail(request)
            if row is not None:
                if row.state is IssuanceStatePersistence.REVOKED:
                    self._sync_revoked_memory(lk, row.provider_issuance_ref)
                    return self._ok(
                        request,
                        IssuanceServiceResult(category=IssuanceOutcomeCategory.REVOKED, safe_ref=None),
                    )
                le = _LedgerEntry(issuance_ref=row.provider_issuance_ref, state=_LedgerState.ISSUED)
        if le is None:
            return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.UNSAFE_TO_DELIVER))
        if le.state is _LedgerState.REVOKED:
            return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.REVOKED, safe_ref=None))

        rsk = (u, request.idempotency_key)
        if rsk in self._resend_cached:
            return self._ok(
                request,
                IssuanceServiceResult(
                    category=IssuanceOutcomeCategory.DELIVERY_READY, safe_ref=self._resend_cached[rsk]
                ),
            )

        g = await self._provider.get_safe_delivery_instructions(
            internal_user_id=u, issuance_ref=le.issuance_ref, correlation_id=request.correlation_id
        )
        if g.outcome is GetSafeInstructionOutcome.READY and g.instruction_ref:
            self._resend_cached[rsk] = g.instruction_ref
            return self._ok(
                request,
                IssuanceServiceResult(category=IssuanceOutcomeCategory.DELIVERY_READY, safe_ref=g.instruction_ref),
            )
        if g.outcome is GetSafeInstructionOutcome.UNAVAILABLE:
            return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.PROVIDER_UNAVAILABLE))
        if g.outcome is GetSafeInstructionOutcome.REJECTED:
            return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.PROVIDER_REJECTED))
        return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.UNSAFE_TO_DELIVER))

    async def _execute_revoke(self, request: IssuanceRequest) -> IssuanceServiceResult:
        u = request.internal_user_id
        assert request.link_issue_idempotency_key is not None
        link = request.link_issue_idempotency_key
        lk = _issuance_ledger_key(u, link)
        rdone = (u, request.idempotency_key)
        if rdone in self._revoke_completed:
            return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.REVOKED, safe_ref=None))

        le = self._get_ledger(u, link)
        store = self._operational_state
        if le is None and store is not None:
            try:
                row = await store.fetch_by_issue_keys(internal_user_id=u, issue_idempotency_key=link)
            except (PersistenceDependencyError, ValueError):
                return self._persist_fail(request)
            if row is None:
                return self._ok(
                    request, IssuanceServiceResult(category=IssuanceOutcomeCategory.NOT_ENTITLED, safe_ref=None)
                )
            if row.state is IssuanceStatePersistence.REVOKED:
                self._revoke_completed.add(rdone)
                self._sync_revoked_memory(lk, row.provider_issuance_ref)
                return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.REVOKED, safe_ref=None))
            le = _LedgerEntry(issuance_ref=row.provider_issuance_ref, state=_LedgerState.ISSUED)

        if le is None:
            return self._ok(
                request, IssuanceServiceResult(category=IssuanceOutcomeCategory.NOT_ENTITLED, safe_ref=None)
            )
        if le.state is _LedgerState.REVOKED:
            self._revoke_completed.add(rdone)
            return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.REVOKED, safe_ref=None))

        rr = await self._provider.revoke_access(
            internal_user_id=u,
            issuance_ref=le.issuance_ref,
            idempotency_key=request.idempotency_key,
            correlation_id=request.correlation_id,
        )
        if rr.outcome is RevokeAccessOutcome.REVOKED or rr.outcome is RevokeAccessOutcome.ALREADY_REVOKED:
            if store is not None:
                try:
                    persisted = await store.mark_revoked(internal_user_id=u, issue_idempotency_key=link)
                except (PersistenceDependencyError, ValueError):
                    return self._persist_fail(request)
                if persisted is None:
                    return self._persist_fail(request)
                self._sync_revoked_memory(lk, persisted.provider_issuance_ref)
            else:
                self._sync_revoked_memory(lk, le.issuance_ref)
            self._revoke_completed.add(rdone)
            return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.REVOKED, safe_ref=None))
        if rr.outcome is RevokeAccessOutcome.UNAVAILABLE:
            return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.PROVIDER_UNAVAILABLE))
        if rr.outcome is RevokeAccessOutcome.REJECTED:
            return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.PROVIDER_REJECTED))
        return self._ok(request, IssuanceServiceResult(category=IssuanceOutcomeCategory.INTERNAL_ERROR))


__all__ = ["IssuanceService"]
