"""Provider-agnostic signed paid-event ingress (no provider SDK, strict fail-closed policy)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from collections.abc import Callable
from typing import Any, Protocol

import asyncpg
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.application.billing_ingestion import NormalizedBillingFactInput
from app.persistence.billing_events_ledger_contracts import BillingEventLedgerStatus
from app.persistence.postgres_billing_ingestion_atomic import PostgresAtomicBillingIngestion
from app.persistence.postgres_billing_subscription_apply import PostgresAtomicUC05SubscriptionApply
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository
from app.security.checkout_reference import (
    DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS,
    DEFAULT_CHECKOUT_REFERENCE_MAX_FUTURE_SECONDS,
    verify_signed_checkout_reference,
)
from app.security.config import ConfigurationError
from app.security.validation import ValidationError, validate_telegram_user_id
from app.application.interfaces import SubscriptionSnapshot
from app.bot_transport.message_catalog import render_telegram_outbound_plan
from app.bot_transport.outbound import build_fulfillment_success_notification_plan
from app.persistence.billing_subscription_apply_contracts import BillingSubscriptionApplyOutcome
from app.shared.types import OperationOutcomeCategory

ENV_PAYMENT_FULFILLMENT_HTTP_ENABLE = "PAYMENT_FULFILLMENT_HTTP_ENABLE"
ENV_PAYMENT_FULFILLMENT_SECRET = "PAYMENT_FULFILLMENT_WEBHOOK_SECRET"
ENV_PAYMENT_FULFILLMENT_PROVIDER_KEY = "PAYMENT_FULFILLMENT_PROVIDER_KEY"
ENV_PAYMENT_FULFILLMENT_MAX_AGE_SECONDS = "PAYMENT_FULFILLMENT_MAX_AGE_SECONDS"
ENV_TELEGRAM_CHECKOUT_REFERENCE_SECRET = "TELEGRAM_CHECKOUT_REFERENCE_SECRET"
ENV_TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS = "TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS"
ENV_LAUNCH_PREFLIGHT_STRICT = "LAUNCH_PREFLIGHT_STRICT"
ENV_SUBSCRIPTION_DEFAULT_PERIOD_DAYS = "SUBSCRIPTION_DEFAULT_PERIOD_DAYS"

PAYMENT_SIGNATURE_HEADER = "x-payment-signature"
PAYMENT_TIMESTAMP_HEADER = "x-payment-timestamp"

_DEFAULT_HTTP_PATH = "/billing/fulfillment/webhook"
_DEFAULT_PROVIDER_KEY = "provider_agnostic_v1"
_DEFAULT_MAX_AGE_SECONDS = 300
_STRICT_CHECKOUT_REFERENCE_MAX_AGE_MIN_SECONDS = 10 * 60
_STRICT_CHECKOUT_REFERENCE_MAX_AGE_MAX_SECONDS = 30 * 24 * 60 * 60
_SCHEMA_VERSION = 1
_EVENT_TYPE_SUBSCRIPTION_ACTIVATED = "subscription_activated"
_MAX_SUBSCRIPTION_PERIOD_DAYS = 3660
_ALLOWED_FIELDS = frozenset(
    {
        "schema_version",
        "external_event_id",
        "external_payment_id",
        "telegram_user_id",
        "client_reference_id",
        "client_reference_proof",
        "metadata",
        "period_days",
        "paid_at",
    }
)


class FulfillmentTelemetry(Protocol):
    async def emit(self, *, decision: str, reason_bucket: str) -> None: ...


class FulfillmentActivationTelegramNotifier(Protocol):
    """Best-effort outbound Telegram after durable activation (HTTP response already committed)."""

    async def send_subscription_activated_notice(
        self,
        *,
        telegram_user_id: int,
        text: str,
        reply_markup: dict[str, Any] | None,
        correlation_id: str,
    ) -> None: ...


class NoopFulfillmentTelemetry:
    async def emit(self, *, decision: str, reason_bucket: str) -> None:
        _ = (decision, reason_bucket)


def _truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes")


def _require_non_empty_str(*, data: dict[str, object], key: str, max_len: int) -> str:
    raw = data.get(key)
    if not isinstance(raw, str):
        raise ValidationError(f"{key} must be a string")
    value = raw.strip()
    if not value:
        raise ValidationError(f"{key} is required")
    if len(value) > max_len:
        raise ValidationError(f"{key} exceeds maximum length")
    return value


def _parse_iso_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    s = value.strip()
    if not s:
        raise ValidationError(f"{field_name} is required")
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be a valid ISO-8601 timestamp") from exc
    if dt.tzinfo is None:
        raise ValidationError(f"{field_name} must include timezone offset")
    return dt


def _safe_json_error(status_code: int, error_code: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error_code}, status_code=status_code)


def _signature_hex_for(secret: str, timestamp: str, raw_body: bytes) -> str:
    msg = timestamp.encode("ascii") + b"." + raw_body
    mac = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256)
    return mac.hexdigest()


def _parse_signature_header(raw: str) -> str | None:
    v = raw.strip().lower()
    if not v:
        return None
    if v.startswith("sha256="):
        v = v[len("sha256=") :]
    if len(v) != 64:
        return None
    for c in v:
        if c not in "0123456789abcdef":
            return None
    return v


def _parse_timestamp_header(raw: str) -> int | None:
    s = raw.strip()
    if not s:
        return None
    if not s.isdigit():
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _stale_request(*, now_epoch_s: int, request_epoch_s: int, max_age_seconds: int) -> bool:
    if request_epoch_s > now_epoch_s + 60:
        return True
    return (now_epoch_s - request_epoch_s) > max_age_seconds


@dataclass(frozen=True, slots=True)
class FulfillmentIngressSettings:
    secret: str
    provider_key: str
    max_age_seconds: int
    checkout_reference_secret: str | None = None
    checkout_reference_max_age_seconds: int = DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS
    strict_checkout_reference_required: bool = False
    http_path: str = _DEFAULT_HTTP_PATH
    default_subscription_period_days: int | None = None


@dataclass(frozen=True, slots=True)
class FulfillmentEventPayload:
    external_event_id: str
    external_payment_id: str
    telegram_user_id: int | None
    client_reference_id: str | None
    client_reference_proof: str | None
    period_days: int | None
    paid_at: datetime


def load_fulfillment_ingress_settings_from_env() -> FulfillmentIngressSettings | None:
    if not _truthy(os.environ.get(ENV_PAYMENT_FULFILLMENT_HTTP_ENABLE)):
        return None
    secret = os.environ.get(ENV_PAYMENT_FULFILLMENT_SECRET, "").strip()
    if not secret:
        raise ConfigurationError(f"missing or empty configuration: {ENV_PAYMENT_FULFILLMENT_SECRET}")
    provider_key = os.environ.get(ENV_PAYMENT_FULFILLMENT_PROVIDER_KEY, _DEFAULT_PROVIDER_KEY).strip()
    if not provider_key:
        raise ConfigurationError(f"invalid configuration: {ENV_PAYMENT_FULFILLMENT_PROVIDER_KEY}")
    max_age_raw = os.environ.get(ENV_PAYMENT_FULFILLMENT_MAX_AGE_SECONDS, str(_DEFAULT_MAX_AGE_SECONDS)).strip()
    try:
        max_age = int(max_age_raw)
    except ValueError as exc:
        raise ConfigurationError(f"invalid configuration: {ENV_PAYMENT_FULFILLMENT_MAX_AGE_SECONDS}") from exc
    if max_age <= 0 or max_age > 3600:
        raise ConfigurationError(f"invalid configuration: {ENV_PAYMENT_FULFILLMENT_MAX_AGE_SECONDS}")
    checkout_reference_secret = os.environ.get(ENV_TELEGRAM_CHECKOUT_REFERENCE_SECRET, "").strip() or None
    checkout_reference_max_age_raw = os.environ.get(
        ENV_TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS,
        str(DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS),
    ).strip()
    try:
        checkout_reference_max_age_seconds = int(checkout_reference_max_age_raw)
    except ValueError as exc:
        raise ConfigurationError(f"invalid configuration: {ENV_TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS}") from exc
    if checkout_reference_max_age_seconds <= 0:
        raise ConfigurationError(f"invalid configuration: {ENV_TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS}")
    strict_checkout_reference_required = _truthy(os.environ.get(ENV_LAUNCH_PREFLIGHT_STRICT))
    if strict_checkout_reference_required and not checkout_reference_secret:
        raise ConfigurationError(f"missing or empty configuration: {ENV_TELEGRAM_CHECKOUT_REFERENCE_SECRET}")
    if strict_checkout_reference_required and (
        checkout_reference_max_age_seconds < _STRICT_CHECKOUT_REFERENCE_MAX_AGE_MIN_SECONDS
        or checkout_reference_max_age_seconds > _STRICT_CHECKOUT_REFERENCE_MAX_AGE_MAX_SECONDS
    ):
        raise ConfigurationError(f"invalid configuration: {ENV_TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS}")
    default_period_raw = os.environ.get(ENV_SUBSCRIPTION_DEFAULT_PERIOD_DAYS, "").strip()
    default_period_days = None
    if default_period_raw:
        try:
            default_period_days = _parse_period_days(default_period_raw)
        except ValidationError as exc:
            raise ConfigurationError(f"invalid configuration: {ENV_SUBSCRIPTION_DEFAULT_PERIOD_DAYS}") from exc
    return FulfillmentIngressSettings(
        secret=secret,
        provider_key=provider_key,
        max_age_seconds=max_age,
        checkout_reference_secret=checkout_reference_secret,
        checkout_reference_max_age_seconds=checkout_reference_max_age_seconds,
        strict_checkout_reference_required=strict_checkout_reference_required,
        default_subscription_period_days=default_period_days,
    )


def _parse_period_days(value: object) -> int:
    if isinstance(value, bool):
        raise ValidationError("period_days must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.isdigit():
            raise ValidationError("period_days must be a positive integer")
        parsed = int(stripped)
    else:
        raise ValidationError("period_days must be an integer")
    if parsed <= 0 or parsed > _MAX_SUBSCRIPTION_PERIOD_DAYS:
        raise ValidationError("period_days must be within allowed range")
    return parsed


def _parse_event_payload(raw_body: bytes) -> FulfillmentEventPayload:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError("payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("payload root must be object")
    extra = set(payload) - _ALLOWED_FIELDS
    if extra:
        raise ValidationError("payload has unknown fields")
    if payload.get("schema_version") != _SCHEMA_VERSION:
        raise ValidationError("schema_version must be 1")
    external_event_id = _require_non_empty_str(data=payload, key="external_event_id", max_len=256)
    external_payment_id = _require_non_empty_str(data=payload, key="external_payment_id", max_len=256)
    raw_telegram_user_id = payload.get("telegram_user_id")
    telegram_user_id = None if raw_telegram_user_id is None else validate_telegram_user_id(raw_telegram_user_id)
    client_reference_id = payload.get("client_reference_id")
    if client_reference_id is not None and not isinstance(client_reference_id, str):
        raise ValidationError("client_reference_id must be a string")
    client_reference_proof = payload.get("client_reference_proof")
    if client_reference_proof is not None and not isinstance(client_reference_proof, str):
        raise ValidationError("client_reference_proof must be a string")
    metadata = payload.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValidationError("metadata must be an object")
    if isinstance(metadata, dict):
        if client_reference_id is None and isinstance(metadata.get("client_reference_id"), str):
            client_reference_id = metadata["client_reference_id"]
        if client_reference_proof is None and isinstance(metadata.get("client_reference_proof"), str):
            client_reference_proof = metadata["client_reference_proof"]
    period_days = payload.get("period_days")
    if period_days is not None:
        period_days = _parse_period_days(period_days)
    paid_at = _parse_iso_timestamp(payload.get("paid_at"), field_name="paid_at")
    return FulfillmentEventPayload(
        external_event_id=external_event_id,
        external_payment_id=external_payment_id,
        telegram_user_id=telegram_user_id,
        client_reference_id=client_reference_id.strip() if isinstance(client_reference_id, str) else None,
        client_reference_proof=client_reference_proof.strip() if isinstance(client_reference_proof, str) else None,
        period_days=period_days,
        paid_at=paid_at,
    )


def _map_to_internal_user_id(telegram_user_id: int) -> str:
    # Reuse existing identity convention from Postgres user identity adapter.
    return f"u{telegram_user_id}"


def _resolve_identity_from_payload(
    *,
    parsed: FulfillmentEventPayload,
    settings: FulfillmentIngressSettings,
    now_utc: datetime,
) -> tuple[int, str]:
    has_reference = bool(parsed.client_reference_id and parsed.client_reference_proof)
    if settings.strict_checkout_reference_required and not has_reference:
        raise ValidationError("missing checkout reference")
    if has_reference:
        if not settings.checkout_reference_secret:
            raise ValidationError("checkout reference secret not configured")
        verified = verify_signed_checkout_reference(
            reference_id=parsed.client_reference_id or "",
            reference_proof=parsed.client_reference_proof or "",
            secret=settings.checkout_reference_secret,
            now=now_utc,
            max_age_seconds=settings.checkout_reference_max_age_seconds,
            max_future_seconds=DEFAULT_CHECKOUT_REFERENCE_MAX_FUTURE_SECONDS,
        )
        issued_at_raw = verified.issued_at
        if issued_at_raw.endswith(("Z", "z")):
            issued_at_raw = issued_at_raw[:-1] + "+00:00"
        try:
            issued_at_dt = datetime.fromisoformat(issued_at_raw).astimezone(UTC)
        except (ValueError, OverflowError):
            issued_at_dt = None
        if issued_at_dt is not None and parsed.paid_at.astimezone(UTC) < issued_at_dt - timedelta(
            seconds=DEFAULT_CHECKOUT_REFERENCE_MAX_FUTURE_SECONDS
        ):
            raise ValidationError("paid_at precedes checkout reference issued_at")
        if parsed.telegram_user_id is not None and parsed.telegram_user_id != verified.telegram_user_id:
            raise ValidationError("telegram_user_id mismatch with checkout reference")
        internal_user_id = verified.internal_user_id or _map_to_internal_user_id(verified.telegram_user_id)
        return verified.telegram_user_id, internal_user_id
    if parsed.telegram_user_id is None:
        raise ValidationError("telegram_user_id is required")
    return parsed.telegram_user_id, _map_to_internal_user_id(parsed.telegram_user_id)


async def _emit_best_effort(telemetry: FulfillmentTelemetry, *, decision: str, reason_bucket: str) -> None:
    try:
        await telemetry.emit(decision=decision, reason_bucket=reason_bucket)
    except Exception:
        return


async def _send_activation_notice_best_effort(
    notifier: FulfillmentActivationTelegramNotifier,
    *,
    telegram_user_id: int,
    text: str,
    reply_markup: dict[str, Any] | None,
    correlation_id: str,
) -> None:
    try:
        await notifier.send_subscription_activated_notice(
            telegram_user_id=telegram_user_id,
            text=text,
            reply_markup=reply_markup,
            correlation_id=correlation_id,
        )
    except Exception:
        return


def _plan_id_from_period_days(period_days: int) -> str:
    if period_days <= 31:
        return "1m"
    if period_days <= 93:
        return "3m"
    return "6m"


async def _process_referral_commissions_best_effort(
    *,
    pool: asyncpg.Pool,
    payer_internal_user_id: str,
    payment_amount_kopecks: int,
    period_days: int,
    correlation_id: str,
) -> None:
    """Look up referrers and credit commissions. Best-effort: never fails the payment."""
    try:
        from app.domain.referral import build_commissions_for_payment
        from app.persistence.postgres_referral import (
            PostgresReferralBalanceRepository,
            PostgresReferralRelationshipRepository,
            PostgresReferralTransactionRepository,
        )
        from app.persistence.referral_contracts import ReferralTransactionRecord

        rel_repo = PostgresReferralRelationshipRepository(pool)
        bal_repo = PostgresReferralBalanceRepository(pool)
        tx_repo = PostgresReferralTransactionRepository(pool)

        referrers = await rel_repo.find_referrers(payer_internal_user_id)
        if not referrers:
            return

        direct_referrer = None
        indirect_referrer = None
        for r in referrers:
            if r.level == 1:
                direct_referrer = r.referrer_user_id
            if r.level == 2:
                indirect_referrer = r.referrer_user_id

        plan_id = _plan_id_from_period_days(period_days)
        commissions = build_commissions_for_payment(
            payer_user_id=payer_internal_user_id,
            direct_referrer_user_id=direct_referrer,
            indirect_referrer_user_id=indirect_referrer,
            plan_id=plan_id,
            payment_amount_kopecks=payment_amount_kopecks,
        )

        for comm in commissions:
            await bal_repo.credit(comm.referrer_user_id, comm.amount_kopecks)
            tx_record = ReferralTransactionRecord(
                transaction_id=f"ref-{uuid.uuid4()}",
                internal_user_id=comm.referrer_user_id,
                amount_kopecks=comm.amount_kopecks,
                transaction_type="referral_credit",
                related_user_id=comm.payer_user_id,
                related_plan_id=comm.plan_id,
                description=f"level {comm.level} commission",
                created_at=datetime.now(UTC),
            )
            await tx_repo.append_transaction(tx_record)
    except Exception:
        return


def create_payment_fulfillment_ingress_app(
    *,
    pool: asyncpg.Pool,
    settings: FulfillmentIngressSettings,
    telemetry: FulfillmentTelemetry | None = None,
    now_utc_provider: Callable[[], datetime] | None = None,
    activation_telegram_notifier: FulfillmentActivationTelegramNotifier | None = None,
) -> Starlette:
    ingress_telemetry = telemetry or NoopFulfillmentTelemetry()
    resolve_now_utc = now_utc_provider or (lambda: datetime.now(UTC))
    notify_activation = activation_telegram_notifier

    async def _handler(request: Request) -> JSONResponse:
        ts_header = request.headers.get(PAYMENT_TIMESTAMP_HEADER, "")
        sig_header = request.headers.get(PAYMENT_SIGNATURE_HEADER, "")
        ts_epoch = _parse_timestamp_header(ts_header)
        sig = _parse_signature_header(sig_header)
        if ts_epoch is None or sig is None:
            await _emit_best_effort(
                ingress_telemetry,
                decision="rejected",
                reason_bucket="missing_or_invalid_signature_headers",
            )
            return _safe_json_error(401, "unauthorized")

        now_epoch = int(time.time())
        if _stale_request(
            now_epoch_s=now_epoch,
            request_epoch_s=ts_epoch,
            max_age_seconds=settings.max_age_seconds,
        ):
            await _emit_best_effort(
                ingress_telemetry,
                decision="rejected",
                reason_bucket="stale_or_replay_window",
            )
            return _safe_json_error(401, "unauthorized")

        raw_body = await request.body()
        expected = _signature_hex_for(settings.secret, ts_header.strip(), raw_body)
        if not hmac.compare_digest(sig, expected):
            await _emit_best_effort(
                ingress_telemetry,
                decision="rejected",
                reason_bucket="invalid_signature",
            )
            return _safe_json_error(401, "unauthorized")

        try:
            parsed = _parse_event_payload(raw_body)
        except ValidationError:
            await _emit_best_effort(
                ingress_telemetry,
                decision="rejected",
                reason_bucket="invalid_payload",
            )
            return _safe_json_error(400, "invalid_payload")

        try:
            telegram_user_id, internal_user_id = _resolve_identity_from_payload(
                parsed=parsed,
                settings=settings,
                now_utc=resolve_now_utc(),
            )
        except ValidationError:
            await _emit_best_effort(
                ingress_telemetry,
                decision="rejected",
                reason_bucket="invalid_checkout_reference",
            )
            return _safe_json_error(400, "invalid_payload")
        received_at = datetime.now(UTC)
        period_days = parsed.period_days or settings.default_subscription_period_days
        if period_days is None:
            await _emit_best_effort(
                ingress_telemetry,
                decision="rejected",
                reason_bucket="missing_subscription_period",
            )
            return _safe_json_error(400, "invalid_payload")
        active_until_utc = parsed.paid_at + timedelta(days=period_days)
        correlation_id = f"fulfill-{uuid.uuid4()}"
        ingest_input = NormalizedBillingFactInput(
            billing_provider_key=settings.provider_key,
            external_event_id=parsed.external_event_id,
            event_type=_EVENT_TYPE_SUBSCRIPTION_ACTIVATED,
            event_effective_at=parsed.paid_at,
            event_received_at=received_at,
            status=BillingEventLedgerStatus.ACCEPTED,
            ingestion_correlation_id=correlation_id,
            internal_user_id=internal_user_id,
            checkout_attempt_id=parsed.external_payment_id,
            amount_currency=None,
            internal_fact_ref=None,
        )

        try:
            identity_repo = PostgresUserIdentityRepository(pool)
            await identity_repo.create_if_absent(telegram_user_id)
            atomic_ingest = PostgresAtomicBillingIngestion(pool)
            ingest_result = await atomic_ingest.ingest_normalized_billing_fact(ingest_input)
            apply = PostgresAtomicUC05SubscriptionApply(pool)
            apply_result = await apply.apply_by_internal_fact_ref(ingest_result.record.internal_fact_ref)
            if apply_result.operation_outcome in (
                OperationOutcomeCategory.SUCCESS,
                OperationOutcomeCategory.IDEMPOTENT_NOOP,
            ):
                snapshots = PostgresSubscriptionSnapshotReader(pool)
                await snapshots.upsert_state(
                    SubscriptionSnapshot(
                        internal_user_id=internal_user_id,
                        state_label="active",
                        active_until_utc=active_until_utc,
                    )
                )
                if (
                    notify_activation is not None
                    and apply_result.operation_outcome is OperationOutcomeCategory.SUCCESS
                    and not apply_result.idempotent_replay
                    and apply_result.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED
                ):
                    plan = build_fulfillment_success_notification_plan(
                        correlation_id=correlation_id,
                        active_until_ymd=active_until_utc.date().isoformat(),
                    )
                    rendered = render_telegram_outbound_plan(plan)
                    await _send_activation_notice_best_effort(
                        notify_activation,
                        telegram_user_id=telegram_user_id,
                        text=rendered.message_text,
                        reply_markup=rendered.reply_markup,
                        correlation_id=correlation_id,
                    )
                # Referral commission processing (best-effort, after durable activation)
                if (
                    apply_result.operation_outcome is OperationOutcomeCategory.SUCCESS
                    and not apply_result.idempotent_replay
                    and apply_result.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED
                ):
                    await _process_referral_commissions_best_effort(
                        pool=pool,
                        payer_internal_user_id=internal_user_id,
                        payment_amount_kopecks=0,
                        period_days=period_days,
                        correlation_id=correlation_id,
                    )
        except Exception:
            await _emit_best_effort(
                ingress_telemetry,
                decision="rejected",
                reason_bucket="dependency_failure",
            )
            return _safe_json_error(503, "temporarily_unavailable")

        if apply_result.operation_outcome not in (
            OperationOutcomeCategory.SUCCESS,
            OperationOutcomeCategory.IDEMPOTENT_NOOP,
        ):
            await _emit_best_effort(
                ingress_telemetry,
                decision="rejected",
                reason_bucket="apply_failed",
            )
            return _safe_json_error(409, "rejected")

        await _emit_best_effort(
            ingress_telemetry,
            decision="accepted",
            reason_bucket="applied",
        )
        return JSONResponse({"ok": True, "accepted": True}, status_code=200)

    return Starlette(routes=[Route(settings.http_path, _handler, methods=["POST"])])

