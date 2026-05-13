"""Оркестрация UC-01 / UC-02 (без транспорта, без реализаций персистентности)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.application.interfaces import (
    AuditAppender,
    AuditEvent,
    IdempotencyRepository,
    SubscriptionSnapshot,
    SubscriptionSnapshotReader,
    SubscriptionSnapshotWriter,
    UserIdentityRepository,
)
from app.application.telegram_access_resend import IssuanceStateForResendLookup
from app.domain.status_view import map_subscription_status_view
from app.security.errors import (
    InternalErrorCategory,
    PersistenceDependencyError,
    UserSafeErrorCode,
    map_internal_to_user_safe,
)
from app.security.idempotency import build_bootstrap_idempotency_key
from app.security.validation import (
    ValidationError,
    validate_telegram_update_id,
    validate_telegram_user_id,
)
from app.shared.correlation import require_correlation_id
from app.shared.types import (
    OperationOutcomeCategory,
    SafeUserStatusCategory,
    SubscriptionSnapshotState,
)


@dataclass(frozen=True, slots=True)
class BootstrapIdentityInput:
    """Нормализованный ввод UC-01 (транспорт не должен передавать сырые Telegram-нагрузки)."""

    telegram_user_id: int
    telegram_update_id: int
    correlation_id: str


@dataclass(frozen=True, slots=True)
class BootstrapIdentityResult:
    outcome: OperationOutcomeCategory
    correlation_id: str
    internal_user_id: str | None
    user_safe: UserSafeErrorCode | None
    idempotent_replay: bool
    #: Только UC-01 bootstrap: тот же дайджест, что и ``idempotency_records.idempotency_key`` (успешные пути).
    uc01_idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class GetSubscriptionStatusInput:
    """Нормализованный ввод UC-02."""

    telegram_user_id: int
    correlation_id: str


@dataclass(frozen=True, slots=True)
class GetSubscriptionStatusResult:
    outcome: OperationOutcomeCategory
    correlation_id: str
    safe_status: SafeUserStatusCategory
    user_safe: UserSafeErrorCode | None
    active_until_utc: datetime | None = None


def _snapshot_state_from_reader_label(state_label: str) -> SubscriptionSnapshotState:
    try:
        return SubscriptionSnapshotState(state_label)
    except ValueError:
        return SubscriptionSnapshotState.INACTIVE


class BootstrapIdentityHandler:
    """UC-01: валидация → идемпотентность → найти-или-создать идентичность → минимальный аудит → завершить ключ."""

    def __init__(
        self,
        identity: UserIdentityRepository,
        idempotency: IdempotencyRepository,
        audit: AuditAppender,
        snapshot_writer: SubscriptionSnapshotWriter,
    ) -> None:
        self._identity = identity
        self._idempotency = idempotency
        self._audit = audit
        self._snapshot_writer = snapshot_writer

    async def handle(self, inp: BootstrapIdentityInput) -> BootstrapIdentityResult:
        cid = inp.correlation_id
        try:
            validate_telegram_user_id(inp.telegram_user_id)
            validate_telegram_update_id(inp.telegram_update_id)
            require_correlation_id(cid)
        except (ValidationError, ValueError):
            return BootstrapIdentityResult(
                outcome=OperationOutcomeCategory.VALIDATION_FAILURE,
                correlation_id=cid,
                internal_user_id=None,
                user_safe=UserSafeErrorCode.INVALID_INPUT,
                idempotent_replay=False,
            )

        try:
            idem_key = build_bootstrap_idempotency_key(
                inp.telegram_user_id,
                inp.telegram_update_id,
            )
        except ValidationError:
            return BootstrapIdentityResult(
                outcome=OperationOutcomeCategory.VALIDATION_FAILURE,
                correlation_id=cid,
                internal_user_id=None,
                user_safe=UserSafeErrorCode.INVALID_INPUT,
                idempotent_replay=False,
            )

        try:
            record = await self._idempotency.begin_or_get(idem_key)
        except PersistenceDependencyError as e:
            return self._failure(
                cid,
                OperationOutcomeCategory.RETRYABLE_DEPENDENCY,
                e.category,
            )
        except Exception:
            return self._failure(
                cid,
                OperationOutcomeCategory.INTERNAL_FAILURE,
                InternalErrorCategory.UNKNOWN,
            )

        if record.completed:
            try:
                identity = await self._identity.find_by_telegram_user_id(inp.telegram_user_id)
            except PersistenceDependencyError as e:
                return self._failure(
                    cid,
                    OperationOutcomeCategory.RETRYABLE_DEPENDENCY,
                    e.category,
                )
            except Exception:
                return self._failure(
                    cid,
                    OperationOutcomeCategory.INTERNAL_FAILURE,
                    InternalErrorCategory.UNKNOWN,
                )
            if identity is None:
                return self._failure(
                    cid,
                    OperationOutcomeCategory.INTERNAL_FAILURE,
                    InternalErrorCategory.PERSISTENCE_INVARIANT,
                )
            if err := await self._put_default_snapshot_if_absent(cid, identity.internal_user_id):
                return err
            return BootstrapIdentityResult(
                outcome=OperationOutcomeCategory.SUCCESS,
                correlation_id=cid,
                internal_user_id=identity.internal_user_id,
                user_safe=None,
                idempotent_replay=True,
                uc01_idempotency_key=idem_key,
            )

        try:
            identity = await self._identity.create_if_absent(inp.telegram_user_id)
        except PersistenceDependencyError as e:
            return self._failure(
                cid,
                OperationOutcomeCategory.RETRYABLE_DEPENDENCY,
                e.category,
            )
        except Exception:
            return self._failure(
                cid,
                OperationOutcomeCategory.INTERNAL_FAILURE,
                InternalErrorCategory.UNKNOWN,
            )

        if err := await self._put_default_snapshot_if_absent(cid, identity.internal_user_id):
            return err

        try:
            await self._audit.append(
                AuditEvent(
                    correlation_id=cid,
                    operation="uc01_bootstrap_identity",
                    outcome=OperationOutcomeCategory.SUCCESS,
                    internal_category=None,
                )
            )
        except PersistenceDependencyError as e:
            return self._failure(
                cid,
                OperationOutcomeCategory.RETRYABLE_DEPENDENCY,
                e.category,
            )
        except Exception:
            return self._failure(
                cid,
                OperationOutcomeCategory.INTERNAL_FAILURE,
                InternalErrorCategory.UNKNOWN,
            )

        try:
            await self._idempotency.complete(idem_key)
        except PersistenceDependencyError as e:
            return self._failure(
                cid,
                OperationOutcomeCategory.RETRYABLE_DEPENDENCY,
                e.category,
            )
        except Exception:
            return self._failure(
                cid,
                OperationOutcomeCategory.INTERNAL_FAILURE,
                InternalErrorCategory.UNKNOWN,
            )

        return BootstrapIdentityResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            internal_user_id=identity.internal_user_id,
            user_safe=None,
            idempotent_replay=False,
            uc01_idempotency_key=idem_key,
        )

    async def _put_default_snapshot_if_absent(
        self,
        correlation_id: str,
        internal_user_id: str,
    ) -> BootstrapIdentityResult | None:
        try:
            await self._snapshot_writer.put_if_absent(
                SubscriptionSnapshot(
                    internal_user_id=internal_user_id,
                    state_label=SubscriptionSnapshotState.INACTIVE.value,
                ),
            )
        except PersistenceDependencyError as e:
            return self._failure(
                correlation_id,
                OperationOutcomeCategory.RETRYABLE_DEPENDENCY,
                e.category,
            )
        except Exception:
            return self._failure(
                correlation_id,
                OperationOutcomeCategory.INTERNAL_FAILURE,
                InternalErrorCategory.UNKNOWN,
            )
        return None

    def _failure(
        self,
        correlation_id: str,
        outcome: OperationOutcomeCategory,
        internal: InternalErrorCategory,
    ) -> BootstrapIdentityResult:
        return BootstrapIdentityResult(
            outcome=outcome,
            correlation_id=correlation_id,
            internal_user_id=None,
            user_safe=map_internal_to_user_safe(internal),
            idempotent_replay=False,
        )


class GetSubscriptionStatusHandler:
    """UC-02: идентичность только для чтения + снапшот подписки → fail-closed доменное отображение; без аудита."""

    def __init__(
        self,
        identity: UserIdentityRepository,
        snapshots: SubscriptionSnapshotReader,
        issuance_state_lookup: IssuanceStateForResendLookup | None = None,
        now_utc_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._identity = identity
        self._snapshots = snapshots
        self._issuance_state_lookup = issuance_state_lookup
        self._now_utc_provider = now_utc_provider or (lambda: datetime.now(UTC))

    async def handle(self, inp: GetSubscriptionStatusInput) -> GetSubscriptionStatusResult:
        cid = inp.correlation_id
        try:
            validate_telegram_user_id(inp.telegram_user_id)
            require_correlation_id(cid)
        except (ValidationError, ValueError):
            return GetSubscriptionStatusResult(
                outcome=OperationOutcomeCategory.VALIDATION_FAILURE,
                correlation_id=cid,
                safe_status=SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE,
                user_safe=UserSafeErrorCode.INVALID_INPUT,
            )

        try:
            identity = await self._identity.find_by_telegram_user_id(inp.telegram_user_id)
        except PersistenceDependencyError as e:
            return GetSubscriptionStatusResult(
                outcome=OperationOutcomeCategory.RETRYABLE_DEPENDENCY,
                correlation_id=cid,
                safe_status=SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE,
                user_safe=map_internal_to_user_safe(e.category),
            )
        except Exception:
            return GetSubscriptionStatusResult(
                outcome=OperationOutcomeCategory.INTERNAL_FAILURE,
                correlation_id=cid,
                safe_status=SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE,
                user_safe=map_internal_to_user_safe(InternalErrorCategory.UNKNOWN),
            )

        if identity is None:
            return GetSubscriptionStatusResult(
                outcome=OperationOutcomeCategory.NOT_FOUND,
                correlation_id=cid,
                safe_status=SafeUserStatusCategory.NEEDS_BOOTSTRAP,
                user_safe=UserSafeErrorCode.NOT_REGISTERED,
            )

        try:
            snap = await self._snapshots.get_for_user(identity.internal_user_id)
        except PersistenceDependencyError as e:
            return GetSubscriptionStatusResult(
                outcome=OperationOutcomeCategory.RETRYABLE_DEPENDENCY,
                correlation_id=cid,
                safe_status=SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE,
                user_safe=map_internal_to_user_safe(e.category),
            )
        except Exception:
            return GetSubscriptionStatusResult(
                outcome=OperationOutcomeCategory.INTERNAL_FAILURE,
                correlation_id=cid,
                safe_status=SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE,
                user_safe=map_internal_to_user_safe(InternalErrorCategory.UNKNOWN),
            )

        state: SubscriptionSnapshotState | None
        state = None if snap is None else _snapshot_state_from_reader_label(snap.state_label)

        safe = map_subscription_status_view(True, state)
        active_until_utc = snap.active_until_utc if snap is not None else None
        if (
            safe is SafeUserStatusCategory.SUBSCRIPTION_ACTIVE
            and active_until_utc is not None
            and self._now_utc_provider() > active_until_utc
        ):
            safe = SafeUserStatusCategory.SUBSCRIPTION_EXPIRED
        if safe is SafeUserStatusCategory.SUBSCRIPTION_ACTIVE:
            lookup = self._issuance_state_lookup
            if lookup is None:
                safe = SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY
            else:
                try:
                    current = await lookup.get_current_for_user(identity.internal_user_id)
                except PersistenceDependencyError as e:
                    return GetSubscriptionStatusResult(
                        outcome=OperationOutcomeCategory.RETRYABLE_DEPENDENCY,
                        correlation_id=cid,
                        safe_status=SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE,
                        user_safe=map_internal_to_user_safe(e.category),
                    )
                except Exception:
                    return GetSubscriptionStatusResult(
                        outcome=OperationOutcomeCategory.INTERNAL_FAILURE,
                        correlation_id=cid,
                        safe_status=SafeUserStatusCategory.INACTIVE_OR_NOT_ELIGIBLE,
                        user_safe=map_internal_to_user_safe(InternalErrorCategory.UNKNOWN),
                    )
                if current is None or current.is_revoked:
                    safe = SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY
                else:
                    safe = SafeUserStatusCategory.SUBSCRIPTION_ACTIVE_ACCESS_READY
        return GetSubscriptionStatusResult(
            outcome=OperationOutcomeCategory.SUCCESS,
            correlation_id=cid,
            safe_status=safe,
            user_safe=None,
            active_until_utc=active_until_utc,
        )
