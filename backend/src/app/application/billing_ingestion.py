"""Внутренняя загрузка нормализованного биллинг-факта (без HTTP, без парсинга провайдера).

Добавляет в :class:`BillingEventsLedgerRepository`, затем append-only UC-04 аудит биллинг-загрузки
(без сырых нагрузок провайдера). UC-05 apply-to-subscription вне области видимости.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.persistence.billing_events_ledger_contracts import (
    BillingEventAmountCurrency,
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
    BillingEventsLedgerRepository,
)
from app.persistence.billing_ingestion_audit_contracts import (
    BILLING_INGESTION_AUDIT_OPERATION,
    BILLING_INGESTION_OUTCOME_ACCEPTED,
    BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY,
    BillingIngestionAuditAppender,
    BillingIngestionAuditRecord,
)
from app.security.validation import ValidationError

# Разумные верхние границы (TEXT-столбцы; держим вход ограниченным)
_MAX_ID_LEN = 256
_MAX_EVENT_TYPE_LEN = 128
_MAX_CORR_LEN = 256

_REF_SAFE = re.compile(r"^[\w.\-:]{1,256}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _require_non_empty_trimmed(*, name: str, value: str, max_len: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    s = value.strip()
    if not s:
        raise ValidationError(f"{name} is required")
    if len(s) > max_len:
        raise ValidationError(f"{name} exceeds maximum length")
    return s


def _optional_trimmed_id(*, name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string or null")
    s = value.strip()
    if not s:
        return None
    if len(s) > _MAX_ID_LEN:
        raise ValidationError(f"{name} exceeds maximum length")
    if _CTRL.search(s):
        raise ValidationError(f"{name} contains disallowed control characters")
    return s


def _validate_tz_aware(*, name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValidationError(f"{name} must be a datetime")
    if value.tzinfo is None:
        raise ValidationError(f"{name} must be timezone-aware")
    return value


def _validate_amount_currency(value: BillingEventAmountCurrency | None) -> BillingEventAmountCurrency | None:
    if value is None:
        return None
    if not isinstance(value, BillingEventAmountCurrency):
        raise ValidationError("amount_currency must be a BillingEventAmountCurrency or null")
    a = value.amount_minor_units
    c = value.currency_code
    if a is not None and (not isinstance(a, int) or isinstance(a, bool) or a < 0):
        raise ValidationError("amount_minor_units must be a non-negative int or null")
    if c is not None:
        if not isinstance(c, str) or not c.strip():
            raise ValidationError("currency_code must be non-empty or null")
        c_s = c.strip()
        if len(c_s) > 16:
            raise ValidationError("currency_code exceeds maximum length")
    return value


@dataclass(frozen=True, slots=True)
class NormalizedBillingFactInput:
    """Только нормализованные скаляры — без поля сырой нагрузки провайдера."""

    billing_provider_key: str
    external_event_id: str
    event_type: str
    event_effective_at: datetime
    event_received_at: datetime
    status: BillingEventLedgerStatus
    ingestion_correlation_id: str
    internal_user_id: str | None = None
    checkout_attempt_id: str | None = None
    amount_currency: BillingEventAmountCurrency | None = None
    internal_fact_ref: str | None = None


@dataclass(frozen=True, slots=True)
class IngestNormalizedBillingFactResult:
    record: BillingEventLedgerRecord
    is_idempotent_replay: bool
    """True когда загрузка попала в существующую строку (provider, external_event_id) ledger (путь повтора).

    Postgres атомарная загрузка обнаруживает повтор из исхода идемпотентности INSERT, поэтому это True
    даже когда вызывающий повторяет тот же явный ``internal_fact_ref`` для этого external id.
    """


def resolve_internal_fact_ref_for_ingest(provided: str | None) -> str:
    """Валидирует и разрешает :attr:`NormalizedBillingFactInput.internal_fact_ref` в строковую ссылку."""
    if provided is None:
        return str(uuid.uuid4())
    s = provided.strip()
    if not s:
        raise ValidationError("internal_fact_ref is required when provided")
    if len(s) > _MAX_ID_LEN:
        raise ValidationError("internal_fact_ref exceeds maximum length")
    if not _REF_SAFE.fullmatch(s):
        raise ValidationError("internal_fact_ref has invalid format")
    return s


def build_ledger_record_for_ingest(input_: NormalizedBillingFactInput) -> BillingEventLedgerRecord:
    """Отображает валидированный :class:`NormalizedBillingFactInput` в :class:`BillingEventLedgerRecord` (без I/O)."""
    pkey = _require_non_empty_trimmed(
        name="billing_provider_key", value=input_.billing_provider_key, max_len=_MAX_ID_LEN
    )
    ext = _require_non_empty_trimmed(name="external_event_id", value=input_.external_event_id, max_len=_MAX_ID_LEN)
    ev_type = _require_non_empty_trimmed(name="event_type", value=input_.event_type, max_len=_MAX_EVENT_TYPE_LEN)
    corr = _require_non_empty_trimmed(
        name="ingestion_correlation_id", value=input_.ingestion_correlation_id, max_len=_MAX_CORR_LEN
    )
    t_eff = _validate_tz_aware(name="event_effective_at", value=input_.event_effective_at)
    t_rec = _validate_tz_aware(name="event_received_at", value=input_.event_received_at)
    if not isinstance(input_.status, BillingEventLedgerStatus):
        raise ValidationError("status must be a BillingEventLedgerStatus value")

    internal_user_id = _optional_trimmed_id(name="internal_user_id", value=input_.internal_user_id)
    checkout = _optional_trimmed_id(name="checkout_attempt_id", value=input_.checkout_attempt_id)

    amount = _validate_amount_currency(input_.amount_currency)
    internal_ref = resolve_internal_fact_ref_for_ingest(input_.internal_fact_ref)

    return BillingEventLedgerRecord(
        internal_fact_ref=internal_ref,
        billing_provider_key=pkey,
        external_event_id=ext,
        event_type=ev_type,
        event_effective_at=t_eff,
        event_received_at=t_rec,
        internal_user_id=internal_user_id,
        checkout_attempt_id=checkout,
        amount_currency=amount,
        status=input_.status,
        ingestion_correlation_id=corr,
    )


class IngestNormalizedBillingFactHandler:
    """Валидирует :class:`NormalizedBillingFactInput` и добавляет в биллинг-леджер событий."""

    def __init__(self, ledger: BillingEventsLedgerRepository, audit: BillingIngestionAuditAppender) -> None:
        self._ledger = ledger
        self._audit = audit

    def _build_record(self, input_: NormalizedBillingFactInput) -> BillingEventLedgerRecord:
        return build_ledger_record_for_ingest(input_)

    async def handle(self, input_: NormalizedBillingFactInput) -> IngestNormalizedBillingFactResult:
        """Сохраняет нормализованный факт (идемпотентный по provider + external_event_id)."""
        constructed = self._build_record(input_)
        stored = await self._ledger.append_or_get_by_provider_and_external_id(constructed)
        # Попадание идемпотентности: тот же (provider, external_id) уже сохранён с другим internal ref
        # (типично, когда internal_fact_ref генерируется автоматически для каждого запроса).
        is_replay = stored.internal_fact_ref != constructed.internal_fact_ref
        audit_outcome = BILLING_INGESTION_OUTCOME_IDEMPOTENT_REPLAY if is_replay else BILLING_INGESTION_OUTCOME_ACCEPTED
        # Fail-closed: :class:`PersistenceDependencyError` от аппендера аудита (напр. Postgres)
        # пробрасывается вызывающему. Неатомарная сборка репозитория: ledger может быть уже закоммичен
        # до записи аудита; операторский Postgres entrypoint использует одну транзакцию БД
        # для ledger+аудита.
        await self._audit.append(
            BillingIngestionAuditRecord(
                internal_fact_ref=stored.internal_fact_ref,
                billing_provider_key=stored.billing_provider_key,
                external_event_id=stored.external_event_id,
                ingestion_correlation_id=stored.ingestion_correlation_id,
                operation=BILLING_INGESTION_AUDIT_OPERATION,
                outcome=audit_outcome,
                billing_event_status=stored.status.value,
                is_idempotent_replay=is_replay,
            )
        )
        return IngestNormalizedBillingFactResult(
            record=stored,
            is_idempotent_replay=is_replay,
        )
