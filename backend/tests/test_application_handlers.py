"""Application orchestration tests with in-memory doubles (UC-01 / UC-02)."""

from __future__ import annotations

import asyncio

from app.application.handlers import (
    BootstrapIdentityHandler,
    BootstrapIdentityInput,
    GetSubscriptionStatusHandler,
    GetSubscriptionStatusInput,
)
from app.application.interfaces import (
    AuditEvent,
    IdempotencyRecord,
    IdentityRecord,
    SubscriptionSnapshot,
)
from app.application.telegram_access_resend import IssuanceCurrentStateRef
from app.security.errors import (
    InternalErrorCategory,
    PersistenceDependencyError,
    UserSafeErrorCode,
)
from app.shared.correlation import new_correlation_id
from app.shared.types import OperationOutcomeCategory, SafeUserStatusCategory, SubscriptionSnapshotState


class _FakeIdentityRepo:
    def __init__(self) -> None:
        self._internal_by_tg: dict[int, str] = {}
        self.create_if_absent_calls = 0

    async def find_by_telegram_user_id(self, telegram_user_id: int) -> IdentityRecord | None:
        iid = self._internal_by_tg.get(telegram_user_id)
        if iid is None:
            return None
        return IdentityRecord(internal_user_id=iid, telegram_user_id=telegram_user_id)

    async def create_if_absent(self, telegram_user_id: int) -> IdentityRecord:
        self.create_if_absent_calls += 1
        if telegram_user_id in self._internal_by_tg:
            return IdentityRecord(
                internal_user_id=self._internal_by_tg[telegram_user_id],
                telegram_user_id=telegram_user_id,
            )
        internal = f"u{telegram_user_id}"
        self._internal_by_tg[telegram_user_id] = internal
        return IdentityRecord(internal_user_id=internal, telegram_user_id=telegram_user_id)


class _FakeIdempotencyRepo:
    def __init__(self) -> None:
        self._completed: dict[str, bool] = {}

    async def get(self, key: str) -> IdempotencyRecord | None:
        if key not in self._completed:
            return None
        return IdempotencyRecord(key=key, completed=self._completed[key])

    async def begin_or_get(self, key: str) -> IdempotencyRecord:
        if key not in self._completed:
            self._completed[key] = False
        return IdempotencyRecord(key=key, completed=self._completed[key])

    async def complete(self, key: str) -> None:
        self._completed[key] = True


class _FakeAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        self.events.append(event)


class _FakeSnapshotWriter:
    async def put_if_absent(self, snapshot: SubscriptionSnapshot) -> None:
        return None

    async def upsert_state(self, snapshot: SubscriptionSnapshot) -> None:
        return None


class _FakeSnapshots:
    def __init__(self, by_user: dict[str, SubscriptionSnapshot | None]) -> None:
        self._by_user = by_user

    async def get_for_user(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        return self._by_user.get(internal_user_id)


class _FakeIssuanceStateLookup:
    def __init__(self, by_user: dict[str, IssuanceCurrentStateRef | None]) -> None:
        self._by_user = by_user

    async def get_current_for_user(self, internal_user_id: str) -> IssuanceCurrentStateRef | None:
        return self._by_user.get(internal_user_id)


def _run(coro):
    return asyncio.run(coro)


def test_bootstrap_creates_identity_once() -> None:
    async def main() -> None:
        ident = _FakeIdentityRepo()
        idem = _FakeIdempotencyRepo()
        audit = _FakeAudit()
        h = BootstrapIdentityHandler(ident, idem, audit, _FakeSnapshotWriter())
        cid = new_correlation_id()
        inp = BootstrapIdentityInput(telegram_user_id=100, telegram_update_id=1, correlation_id=cid)
        r = await h.handle(inp)
        assert r.outcome is OperationOutcomeCategory.SUCCESS
        assert r.internal_user_id == "u100"
        assert r.idempotent_replay is False
        assert ident.create_if_absent_calls == 1
        assert len(audit.events) == 1
        assert audit.events[0].operation == "uc01_bootstrap_identity"
        assert audit.events[0].correlation_id == cid

    _run(main())


def test_duplicate_bootstrap_idempotent_no_second_create() -> None:
    async def main() -> None:
        ident = _FakeIdentityRepo()
        idem = _FakeIdempotencyRepo()
        audit = _FakeAudit()
        h = BootstrapIdentityHandler(ident, idem, audit, _FakeSnapshotWriter())
        cid = new_correlation_id()
        inp = BootstrapIdentityInput(telegram_user_id=7, telegram_update_id=99, correlation_id=cid)
        r1 = await h.handle(inp)
        r2 = await h.handle(inp)
        assert r1.outcome is OperationOutcomeCategory.SUCCESS
        assert r2.outcome is OperationOutcomeCategory.SUCCESS
        assert r2.idempotent_replay is True
        assert ident.create_if_absent_calls == 1
        assert r1.internal_user_id == r2.internal_user_id
        assert len(audit.events) == 1

    _run(main())


def test_bootstrap_repository_failure_no_success() -> None:
    async def main() -> None:
        class FlakyIdentity(_FakeIdentityRepo):
            async def create_if_absent(self, telegram_user_id: int) -> IdentityRecord:
                raise PersistenceDependencyError(InternalErrorCategory.PERSISTENCE_TRANSIENT)

        ident = FlakyIdentity()
        h = BootstrapIdentityHandler(ident, _FakeIdempotencyRepo(), _FakeAudit(), _FakeSnapshotWriter())
        cid = new_correlation_id()
        r = await h.handle(
            BootstrapIdentityInput(telegram_user_id=1, telegram_update_id=2, correlation_id=cid),
        )
        assert r.outcome is OperationOutcomeCategory.RETRYABLE_DEPENDENCY
        assert r.internal_user_id is None
        assert r.user_safe is UserSafeErrorCode.TRY_AGAIN_LATER

    _run(main())


def test_get_status_unknown_user_onboarding_outcome() -> None:
    async def main() -> None:
        ident = _FakeIdentityRepo()
        h = GetSubscriptionStatusHandler(ident, _FakeSnapshots({}))
        cid = new_correlation_id()
        r = await h.handle(GetSubscriptionStatusInput(telegram_user_id=404, correlation_id=cid))
        assert r.outcome is OperationOutcomeCategory.NOT_FOUND
        assert r.safe_status is SafeUserStatusCategory.NEEDS_BOOTSTRAP
        assert r.user_safe is UserSafeErrorCode.NOT_REGISTERED
        assert r.correlation_id == cid

    _run(main())


def test_get_status_known_default_inactive_fail_closed() -> None:
    async def main() -> None:
        ident = _FakeIdentityRepo()
        await ident.create_if_absent(50)
        snap = SubscriptionSnapshot(internal_user_id="u50", state_label="inactive")
        h = GetSubscriptionStatusHandler(ident, _FakeSnapshots({"u50": snap}))
        cid = new_correlation_id()
        r = await h.handle(GetSubscriptionStatusInput(telegram_user_id=50, correlation_id=cid))
        assert r.outcome is OperationOutcomeCategory.SUCCESS
        assert r.safe_status is SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE
        assert r.user_safe is None

    _run(main())


def test_get_status_known_user_needs_review_snapshot() -> None:
    async def main() -> None:
        ident = _FakeIdentityRepo()
        await ident.create_if_absent(51)
        snap = SubscriptionSnapshot(internal_user_id="u51", state_label="needs_review")
        h = GetSubscriptionStatusHandler(ident, _FakeSnapshots({"u51": snap}))
        cid = new_correlation_id()
        r = await h.handle(GetSubscriptionStatusInput(telegram_user_id=51, correlation_id=cid))
        assert r.outcome is OperationOutcomeCategory.SUCCESS
        assert r.safe_status is SafeUserStatusCategory.NEEDS_REVIEW
        assert r.user_safe is None

    _run(main())


def test_get_status_active_subscription_without_readiness_lookup_is_fail_closed_not_ready() -> None:
    async def main() -> None:
        ident = _FakeIdentityRepo()
        await ident.create_if_absent(52)
        snap = SubscriptionSnapshot(internal_user_id="u52", state_label=SubscriptionSnapshotState.ACTIVE.value)
        h = GetSubscriptionStatusHandler(ident, _FakeSnapshots({"u52": snap}))
        cid = new_correlation_id()
        r = await h.handle(GetSubscriptionStatusInput(telegram_user_id=52, correlation_id=cid))
        assert r.outcome is OperationOutcomeCategory.SUCCESS
        assert r.safe_status is SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY

    _run(main())


def test_get_status_active_subscription_without_issued_access_is_not_ready() -> None:
    async def main() -> None:
        ident = _FakeIdentityRepo()
        await ident.create_if_absent(520)
        snap = SubscriptionSnapshot(
            internal_user_id="u520",
            state_label=SubscriptionSnapshotState.ACTIVE.value,
        )
        h = GetSubscriptionStatusHandler(
            ident,
            _FakeSnapshots({"u520": snap}),
            issuance_state_lookup=_FakeIssuanceStateLookup({"u520": None}),
        )
        cid = new_correlation_id()
        r = await h.handle(GetSubscriptionStatusInput(telegram_user_id=520, correlation_id=cid))
        assert r.outcome is OperationOutcomeCategory.SUCCESS
        assert r.safe_status is SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY

    _run(main())


def test_get_status_active_subscription_with_issued_access_is_ready() -> None:
    async def main() -> None:
        ident = _FakeIdentityRepo()
        await ident.create_if_absent(521)
        snap = SubscriptionSnapshot(
            internal_user_id="u521",
            state_label=SubscriptionSnapshotState.ACTIVE.value,
        )
        h = GetSubscriptionStatusHandler(
            ident,
            _FakeSnapshots({"u521": snap}),
            issuance_state_lookup=_FakeIssuanceStateLookup(
                {
                    "u521": IssuanceCurrentStateRef(
                        issue_idempotency_key="issue-521",
                        is_revoked=False,
                    )
                }
            ),
        )
        cid = new_correlation_id()
        r = await h.handle(GetSubscriptionStatusInput(telegram_user_id=521, correlation_id=cid))
        assert r.outcome is OperationOutcomeCategory.SUCCESS
        assert r.safe_status is SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_READY

    _run(main())


def test_get_status_no_audit_dependency() -> None:
    """UC-02 handler has no audit appender; orchestration stays read-only."""

    async def main() -> None:
        ident = _FakeIdentityRepo()
        await ident.create_if_absent(1)
        h = GetSubscriptionStatusHandler(ident, _FakeSnapshots({"u1": None}))
        await h.handle(
            GetSubscriptionStatusInput(telegram_user_id=1, correlation_id=new_correlation_id()),
        )

    _run(main())


def test_correlation_id_echoed_in_results() -> None:
    async def main() -> None:
        cid = new_correlation_id()
        ident = _FakeIdentityRepo()
        h_boot = BootstrapIdentityHandler(ident, _FakeIdempotencyRepo(), _FakeAudit(), _FakeSnapshotWriter())
        r1 = await h_boot.handle(
            BootstrapIdentityInput(telegram_user_id=2, telegram_update_id=3, correlation_id=cid),
        )
        assert r1.correlation_id == cid
        h_stat = GetSubscriptionStatusHandler(ident, _FakeSnapshots({}))
        r2 = await h_stat.handle(GetSubscriptionStatusInput(telegram_user_id=999, correlation_id=cid))
        assert r2.correlation_id == cid

    _run(main())


def test_handlers_do_not_require_raw_payload() -> None:
    """Inputs are primitive fields only (normalized boundary)."""
    fields_boot = BootstrapIdentityInput.__dataclass_fields__
    assert set(fields_boot) == {"telegram_user_id", "telegram_update_id", "correlation_id"}
    fields_stat = GetSubscriptionStatusInput.__dataclass_fields__
    assert set(fields_stat) == {"telegram_user_id", "correlation_id"}
