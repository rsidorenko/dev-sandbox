from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.application.interfaces import IdentityRecord, SubscriptionSnapshot
from app.application.telegram_access_resend import (
    InMemoryAccessResendCooldownStore,
    IssuanceCurrentStateRef,
    TelegramAccessResendDisabledHitEvent,
    TelegramAccessResendHandler,
    TelegramAccessResendInput,
    TelegramAccessResendOutcome,
    TelegramAccessResendSourceCommand,
    build_telegram_resend_idempotency_key,
)
from app.issuance.contracts import IssuanceOutcomeCategory, IssuanceServiceResult
from app.shared.correlation import new_correlation_id


class _IdentityRepo:
    def __init__(self, record: IdentityRecord | None) -> None:
        self._record = record
        self.calls = 0

    async def find_by_telegram_user_id(self, telegram_user_id: int) -> IdentityRecord | None:
        self.calls += 1
        return self._record


class _Snapshots:
    def __init__(self, snapshot: SubscriptionSnapshot | None) -> None:
        self._snapshot = snapshot
        self.calls = 0

    async def get_for_user(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        self.calls += 1
        return self._snapshot


class _StateLookup:
    def __init__(self, state: IssuanceCurrentStateRef | None) -> None:
        self._state = state
        self.calls = 0

    async def get_current_for_user(self, internal_user_id: str) -> IssuanceCurrentStateRef | None:
        self.calls += 1
        return self._state


class _StateMutationSpy:
    def __init__(self) -> None:
        self.calls = 0
        self.last_internal_user_id: str | None = None
        self.last_issue_idempotency_key: str | None = None

    async def mark_revoked(self, *, internal_user_id: str, issue_idempotency_key: str) -> object | None:
        self.calls += 1
        self.last_internal_user_id = internal_user_id
        self.last_issue_idempotency_key = issue_idempotency_key
        return object()


class _ServiceSpy:
    def __init__(self, result: IssuanceServiceResult) -> None:
        self.result = result
        self.calls = 0
        self.last_request = None

    async def execute(self, request):
        self.calls += 1
        self.last_request = request
        return self.result


class _CooldownSpy:
    def __init__(self, *, allowed: bool = True) -> None:
        self.calls = 0
        self._allowed = allowed

    async def consume_or_reject(self, internal_user_id: str, now_epoch_seconds: float) -> bool:
        self.calls += 1
        return self._allowed


class _DisabledMarkerSpy:
    def __init__(self) -> None:
        self.calls = 0
        self.events: list[TelegramAccessResendDisabledHitEvent] = []

    def record_disabled_hit(self, event: TelegramAccessResendDisabledHitEvent) -> None:
        self.calls += 1
        self.events.append(event)


def _inp(
    update_id: int = 10,
    source_command: TelegramAccessResendSourceCommand | None = None,
) -> TelegramAccessResendInput:
    return TelegramAccessResendInput(
        telegram_user_id=42,
        telegram_update_id=update_id,
        correlation_id=new_correlation_id(),
        source_command=source_command,
    )


@pytest.mark.asyncio
async def test_active_entitled_calls_issuance_resend() -> None:
    service = _ServiceSpy(IssuanceServiceResult(category=IssuanceOutcomeCategory.DELIVERY_READY, safe_ref="x"))
    marker = _DisabledMarkerSpy()
    h = TelegramAccessResendHandler(
        identity=_IdentityRepo(IdentityRecord(internal_user_id="u42", telegram_user_id=42)),
        snapshots=_Snapshots(SubscriptionSnapshot(internal_user_id="u42", state_label="active")),
        issuance_service=service,  # type: ignore[arg-type]
        issuance_state_lookup=_StateLookup(IssuanceCurrentStateRef(issue_idempotency_key="issue-1", is_revoked=False)),
        issuance_state_mutation=None,
        cooldown=InMemoryAccessResendCooldownStore(cooldown_seconds=60),
        disabled_hit_marker=marker,
        enabled=True,
        now_seconds=lambda: 1000.0,
    )
    out = await h.handle(_inp(update_id=333))
    assert out.outcome is TelegramAccessResendOutcome.RESEND_ACCEPTED
    assert service.calls == 1
    assert service.last_request.operation.value == "resend"
    assert service.last_request.link_issue_idempotency_key == "issue-1"
    assert service.last_request.idempotency_key == "tg-resend:42:333"
    assert marker.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("state_label", ("inactive", "needs_review", "not_eligible", "absent"))
async def test_non_active_entitlement_no_issuance_call(state_label: str) -> None:
    service = _ServiceSpy(IssuanceServiceResult(category=IssuanceOutcomeCategory.DELIVERY_READY))
    h = TelegramAccessResendHandler(
        identity=_IdentityRepo(IdentityRecord(internal_user_id="u42", telegram_user_id=42)),
        snapshots=_Snapshots(SubscriptionSnapshot(internal_user_id="u42", state_label=state_label)),
        issuance_service=service,  # type: ignore[arg-type]
        issuance_state_lookup=_StateLookup(IssuanceCurrentStateRef(issue_idempotency_key="issue-1", is_revoked=False)),
        issuance_state_mutation=None,
        cooldown=InMemoryAccessResendCooldownStore(cooldown_seconds=60),
        enabled=True,
        now_seconds=lambda: 1.0,
    )
    out = await h.handle(_inp())
    assert out.outcome is TelegramAccessResendOutcome.NOT_ELIGIBLE
    assert service.calls == 0


@pytest.mark.asyncio
async def test_unknown_user_is_safe_denial_no_issuance_call() -> None:
    service = _ServiceSpy(IssuanceServiceResult(category=IssuanceOutcomeCategory.DELIVERY_READY))
    h = TelegramAccessResendHandler(
        identity=_IdentityRepo(None),
        snapshots=_Snapshots(SubscriptionSnapshot(internal_user_id="u42", state_label="active")),
        issuance_service=service,  # type: ignore[arg-type]
        issuance_state_lookup=_StateLookup(IssuanceCurrentStateRef(issue_idempotency_key="issue-1", is_revoked=False)),
        issuance_state_mutation=None,
        cooldown=InMemoryAccessResendCooldownStore(cooldown_seconds=60),
        enabled=True,
    )
    out = await h.handle(_inp())
    assert out.outcome is TelegramAccessResendOutcome.NOT_ELIGIBLE
    assert service.calls == 0


@pytest.mark.asyncio
async def test_missing_snapshot_is_safe_denial_no_issuance_call() -> None:
    service = _ServiceSpy(IssuanceServiceResult(category=IssuanceOutcomeCategory.DELIVERY_READY))
    h = TelegramAccessResendHandler(
        identity=_IdentityRepo(IdentityRecord(internal_user_id="u42", telegram_user_id=42)),
        snapshots=_Snapshots(None),
        issuance_service=service,  # type: ignore[arg-type]
        issuance_state_lookup=_StateLookup(IssuanceCurrentStateRef(issue_idempotency_key="issue-1", is_revoked=False)),
        issuance_state_mutation=None,
        cooldown=InMemoryAccessResendCooldownStore(cooldown_seconds=60),
        enabled=True,
    )
    out = await h.handle(_inp())
    assert out.outcome is TelegramAccessResendOutcome.NOT_ELIGIBLE
    assert service.calls == 0


@pytest.mark.asyncio
async def test_cooldown_hit_blocks_issuance_call() -> None:
    service = _ServiceSpy(IssuanceServiceResult(category=IssuanceOutcomeCategory.DELIVERY_READY))
    h = TelegramAccessResendHandler(
        identity=_IdentityRepo(IdentityRecord(internal_user_id="u42", telegram_user_id=42)),
        snapshots=_Snapshots(SubscriptionSnapshot(internal_user_id="u42", state_label="active")),
        issuance_service=service,  # type: ignore[arg-type]
        issuance_state_lookup=_StateLookup(IssuanceCurrentStateRef(issue_idempotency_key="issue-1", is_revoked=False)),
        issuance_state_mutation=None,
        cooldown=InMemoryAccessResendCooldownStore(cooldown_seconds=60),
        enabled=True,
        now_seconds=lambda: 100.0,
    )
    first = await h.handle(_inp(update_id=1))
    second = await h.handle(_inp(update_id=2))
    assert first.outcome is TelegramAccessResendOutcome.RESEND_ACCEPTED
    assert second.outcome is TelegramAccessResendOutcome.COOLDOWN
    assert service.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category", "expected"),
    (
        (IssuanceOutcomeCategory.UNSAFE_TO_DELIVER, TelegramAccessResendOutcome.NOT_READY),
        (IssuanceOutcomeCategory.REVOKED, TelegramAccessResendOutcome.NOT_READY),
        (IssuanceOutcomeCategory.PROVIDER_UNAVAILABLE, TelegramAccessResendOutcome.TEMPORARILY_UNAVAILABLE),
        (IssuanceOutcomeCategory.INTERNAL_ERROR, TelegramAccessResendOutcome.TEMPORARILY_UNAVAILABLE),
    ),
)
async def test_issuance_outcomes_map_to_safe_handler_outcomes(
    category: IssuanceOutcomeCategory, expected: TelegramAccessResendOutcome
) -> None:
    service = _ServiceSpy(IssuanceServiceResult(category=category))
    h = TelegramAccessResendHandler(
        identity=_IdentityRepo(IdentityRecord(internal_user_id="u42", telegram_user_id=42)),
        snapshots=_Snapshots(SubscriptionSnapshot(internal_user_id="u42", state_label="active")),
        issuance_service=service,  # type: ignore[arg-type]
        issuance_state_lookup=_StateLookup(IssuanceCurrentStateRef(issue_idempotency_key="issue-1", is_revoked=False)),
        issuance_state_mutation=None,
        cooldown=InMemoryAccessResendCooldownStore(cooldown_seconds=0),
        enabled=True,
    )
    out = await h.handle(_inp())
    assert out.outcome is expected


def test_resend_idempotency_key_is_deterministic() -> None:
    assert build_telegram_resend_idempotency_key(77, 9) == "tg-resend:77:9"


@pytest.mark.asyncio
async def test_flag_disabled_short_circuits_before_entitlement_cooldown_and_issuance() -> None:
    identity = _IdentityRepo(IdentityRecord(internal_user_id="u42", telegram_user_id=42))
    snapshots = _Snapshots(SubscriptionSnapshot(internal_user_id="u42", state_label="active"))
    state_lookup = _StateLookup(IssuanceCurrentStateRef(issue_idempotency_key="issue-1", is_revoked=False))
    service = _ServiceSpy(IssuanceServiceResult(category=IssuanceOutcomeCategory.DELIVERY_READY))
    cooldown = _CooldownSpy(allowed=True)
    marker = _DisabledMarkerSpy()
    h = TelegramAccessResendHandler(
        identity=identity,
        snapshots=snapshots,
        issuance_service=service,  # type: ignore[arg-type]
        issuance_state_lookup=state_lookup,
        issuance_state_mutation=None,
        cooldown=cooldown,
        disabled_hit_marker=marker,
        enabled=False,
        now_seconds=lambda: 100.0,
    )
    out = await h.handle(_inp(source_command=TelegramAccessResendSourceCommand.RESEND_ACCESS))
    assert out.outcome is TelegramAccessResendOutcome.NOT_ENABLED
    assert marker.calls == 1
    assert marker.events == [
        TelegramAccessResendDisabledHitEvent(
            source_command=TelegramAccessResendSourceCommand.RESEND_ACCESS,
        )
    ]
    assert identity.calls == 0
    assert snapshots.calls == 0
    assert cooldown.calls == 0
    assert state_lookup.calls == 0
    assert service.calls == 0


def test_disabled_hit_event_contains_only_bounded_safe_fields() -> None:
    event = TelegramAccessResendDisabledHitEvent(
        source_command=TelegramAccessResendSourceCommand.GET_ACCESS,
    )
    payload = {
        "operation": event.operation,
        "outcome": event.outcome,
        "source_command": event.source_command.value if event.source_command is not None else None,
    }
    assert payload == {
        "operation": "telegram_access_resend",
        "outcome": "not_enabled",
        "source_command": "get_access",
    }
    blob = f"{payload}".lower()
    forbidden = (
        "telegram_user_id",
        "internal_user_id",
        "chat_id",
        "message_text",
        "payload",
        "idempotency_key",
        "provider_issuance_ref",
        "database_url",
        "postgres://",
        "bearer ",
        "private key",
    )
    for key in forbidden:
        assert key not in blob


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (TelegramAccessResendSourceCommand.RESEND_ACCESS, "resend_access"),
        (TelegramAccessResendSourceCommand.GET_ACCESS, "get_access"),
    ),
)
async def test_disabled_marker_records_bounded_source_command(
    source: TelegramAccessResendSourceCommand,
    expected: str,
) -> None:
    marker = _DisabledMarkerSpy()
    h = TelegramAccessResendHandler(
        identity=_IdentityRepo(IdentityRecord(internal_user_id="u42", telegram_user_id=42)),
        snapshots=_Snapshots(SubscriptionSnapshot(internal_user_id="u42", state_label="active")),
        issuance_service=_ServiceSpy(IssuanceServiceResult(category=IssuanceOutcomeCategory.DELIVERY_READY)),  # type: ignore[arg-type]
        issuance_state_lookup=_StateLookup(
            IssuanceCurrentStateRef(issue_idempotency_key="issue-1", is_revoked=False),
        ),
        issuance_state_mutation=None,
        cooldown=_CooldownSpy(allowed=True),
        disabled_hit_marker=marker,
        enabled=False,
    )
    out = await h.handle(_inp(source_command=source))
    assert out.outcome is TelegramAccessResendOutcome.NOT_ENABLED
    assert marker.calls == 1
    assert marker.events[0].source_command is source
    assert marker.events[0].source_command.value == expected


@pytest.mark.asyncio
async def test_expired_subscription_denies_and_marks_existing_issued_access_revoked() -> None:
    service = _ServiceSpy(IssuanceServiceResult(category=IssuanceOutcomeCategory.DELIVERY_READY))
    mutation = _StateMutationSpy()
    h = TelegramAccessResendHandler(
        identity=_IdentityRepo(IdentityRecord(internal_user_id="u42", telegram_user_id=42)),
        snapshots=_Snapshots(
            SubscriptionSnapshot(
                internal_user_id="u42",
                state_label="active",
                active_until_utc=datetime.now(UTC) - timedelta(days=1),
            )
        ),
        issuance_service=service,  # type: ignore[arg-type]
        issuance_state_lookup=_StateLookup(IssuanceCurrentStateRef(issue_idempotency_key="issue-42", is_revoked=False)),
        issuance_state_mutation=mutation,
        cooldown=InMemoryAccessResendCooldownStore(cooldown_seconds=60),
        enabled=True,
    )
    out = await h.handle(_inp(update_id=808))
    assert out.outcome is TelegramAccessResendOutcome.NOT_ELIGIBLE
    assert mutation.calls == 1
    assert mutation.last_internal_user_id == "u42"
    assert mutation.last_issue_idempotency_key == "issue-42"
    assert service.calls == 0


@pytest.mark.asyncio
async def test_expired_subscription_does_not_mutate_when_current_state_already_revoked() -> None:
    mutation = _StateMutationSpy()
    h = TelegramAccessResendHandler(
        identity=_IdentityRepo(IdentityRecord(internal_user_id="u42", telegram_user_id=42)),
        snapshots=_Snapshots(
            SubscriptionSnapshot(
                internal_user_id="u42",
                state_label="active",
                active_until_utc=datetime.now(UTC) - timedelta(days=1),
            )
        ),
        issuance_service=_ServiceSpy(IssuanceServiceResult(category=IssuanceOutcomeCategory.DELIVERY_READY)),  # type: ignore[arg-type]
        issuance_state_lookup=_StateLookup(IssuanceCurrentStateRef(issue_idempotency_key="issue-42", is_revoked=True)),
        issuance_state_mutation=mutation,
        cooldown=InMemoryAccessResendCooldownStore(cooldown_seconds=60),
        enabled=True,
    )
    out = await h.handle(_inp(update_id=809))
    assert out.outcome is TelegramAccessResendOutcome.NOT_ELIGIBLE
    assert mutation.calls == 0
