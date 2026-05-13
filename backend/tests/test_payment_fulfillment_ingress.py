from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from app.bot_transport.message_catalog import render_telegram_outbound_plan
from app.bot_transport.outbound import build_fulfillment_success_notification_plan
from app.persistence.billing_subscription_apply_contracts import BillingSubscriptionApplyOutcome
from app.runtime import payment_fulfillment_ingress as ingress_mod
from app.runtime.payment_fulfillment_ingress import (
    ENV_PAYMENT_FULFILLMENT_HTTP_ENABLE,
    ENV_PAYMENT_FULFILLMENT_SECRET,
    ENV_TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS,
    FulfillmentIngressSettings,
    create_payment_fulfillment_ingress_app,
    load_fulfillment_ingress_settings_from_env,
)
from app.security.checkout_reference import create_signed_checkout_reference
from app.security.config import ConfigurationError
from app.shared.types import OperationOutcomeCategory


def _sign(secret: str, ts: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), ts.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "external_event_id": "evt-1",
        "external_payment_id": "pay-1",
        "telegram_user_id": 12345,
        "period_days": 30,
        "paid_at": datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC).isoformat(),
    }


def _reference() -> tuple[str, str]:
    signed = create_signed_checkout_reference(
        telegram_user_id=12345,
        internal_user_id="u12345",
        secret="r" * 32,
        now=datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC),
    )
    return signed.reference_id, signed.reference_proof


def _payload_with_reference() -> dict[str, object]:
    ref_id, ref_proof = _reference()
    payload = _payload()
    payload["client_reference_id"] = ref_id
    payload["client_reference_proof"] = ref_proof
    return payload


def _payload_with_reference_issued_at(issued_at: datetime) -> dict[str, object]:
    signed = create_signed_checkout_reference(
        telegram_user_id=12345,
        internal_user_id="u12345",
        secret="r" * 32,
        now=issued_at,
    )
    payload = _payload()
    payload["client_reference_id"] = signed.reference_id
    payload["client_reference_proof"] = signed.reference_proof
    return payload


def _settings(*, strict_reference: bool = False) -> FulfillmentIngressSettings:
    return FulfillmentIngressSettings(
        secret="s" * 32,
        provider_key="provider_agnostic_v1",
        max_age_seconds=300,
        checkout_reference_secret="r" * 32,
        checkout_reference_max_age_seconds=7 * 24 * 60 * 60,
        strict_checkout_reference_required=strict_reference,
        default_subscription_period_days=30,
    )


@pytest.mark.asyncio
async def test_missing_secret_config_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PAYMENT_FULFILLMENT_HTTP_ENABLE, "1")
    monkeypatch.delenv(ENV_PAYMENT_FULFILLMENT_SECRET, raising=False)
    with pytest.raises(ConfigurationError):
        load_fulfillment_ingress_settings_from_env()


@pytest.mark.asyncio
async def test_strict_mode_requires_checkout_reference_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PAYMENT_FULFILLMENT_HTTP_ENABLE, "1")
    monkeypatch.setenv(ENV_PAYMENT_FULFILLMENT_SECRET, "s" * 32)
    monkeypatch.setenv("LAUNCH_PREFLIGHT_STRICT", "1")
    monkeypatch.delenv("TELEGRAM_CHECKOUT_REFERENCE_SECRET", raising=False)
    with pytest.raises(ConfigurationError):
        load_fulfillment_ingress_settings_from_env()


@pytest.mark.asyncio
async def test_strict_mode_rejects_dangerously_small_checkout_reference_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_PAYMENT_FULFILLMENT_HTTP_ENABLE, "1")
    monkeypatch.setenv(ENV_PAYMENT_FULFILLMENT_SECRET, "s" * 32)
    monkeypatch.setenv("LAUNCH_PREFLIGHT_STRICT", "1")
    monkeypatch.setenv("TELEGRAM_CHECKOUT_REFERENCE_SECRET", "r" * 32)
    monkeypatch.setenv(ENV_TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS, "59")
    with pytest.raises(ConfigurationError):
        load_fulfillment_ingress_settings_from_env()


@pytest.mark.asyncio
async def test_strict_mode_rejects_dangerously_large_checkout_reference_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_PAYMENT_FULFILLMENT_HTTP_ENABLE, "1")
    monkeypatch.setenv(ENV_PAYMENT_FULFILLMENT_SECRET, "s" * 32)
    monkeypatch.setenv("LAUNCH_PREFLIGHT_STRICT", "1")
    monkeypatch.setenv("TELEGRAM_CHECKOUT_REFERENCE_SECRET", "r" * 32)
    monkeypatch.setenv(ENV_TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS, str((30 * 24 * 60 * 60) + 1))
    with pytest.raises(ConfigurationError):
        load_fulfillment_ingress_settings_from_env()


def test_invalid_signature_rejects_and_does_not_mutate() -> None:
    app = create_payment_fulfillment_ingress_app(pool=object(), settings=_settings())  # type: ignore[arg-type]
    body = json.dumps(_payload()).encode("utf-8")
    with (
        pytest.MonkeyPatch.context() as m,
        TestClient(app) as client,
    ):
        create_if_absent = AsyncMock()
        ingest = AsyncMock()
        apply = AsyncMock()
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", create_if_absent)
        m.setattr(ingress_mod.PostgresAtomicBillingIngestion, "ingest_normalized_billing_fact", ingest)
        m.setattr(ingress_mod.PostgresAtomicUC05SubscriptionApply, "apply_by_internal_fact_ref", apply)
        r = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: "1777248000",
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + ("0" * 64),
            },
        )
    assert r.status_code == 401
    create_if_absent.assert_not_called()
    ingest.assert_not_called()
    apply.assert_not_called()


def test_malformed_payload_rejects_and_does_not_mutate() -> None:
    app = create_payment_fulfillment_ingress_app(pool=object(), settings=_settings())  # type: ignore[arg-type]
    body = b'{"schema_version":1}'
    ts = "1777248000"
    sig = _sign(_settings().secret, ts, body)
    with (
        pytest.MonkeyPatch.context() as m,
        TestClient(app) as client,
    ):
        create_if_absent = AsyncMock()
        ingest = AsyncMock()
        apply = AsyncMock()
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", create_if_absent)
        m.setattr(ingress_mod.PostgresAtomicBillingIngestion, "ingest_normalized_billing_fact", ingest)
        m.setattr(ingress_mod.PostgresAtomicUC05SubscriptionApply, "apply_by_internal_fact_ref", apply)
        m.setattr(ingress_mod.PostgresSubscriptionSnapshotReader, "upsert_state", AsyncMock())
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        r = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert r.status_code == 400
    create_if_absent.assert_not_called()
    ingest.assert_not_called()
    apply.assert_not_called()


def test_valid_paid_event_applies_and_duplicate_is_idempotent() -> None:
    _now = datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC)
    app = create_payment_fulfillment_ingress_app(
        pool=object(), settings=_settings(strict_reference=True), now_utc_provider=lambda: _now
    )  # type: ignore[arg-type]
    payload = _payload_with_reference()
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings().secret, ts, body)

    class _IngestResult:
        class _Record:
            internal_fact_ref = "fact-1"

        record = _Record()

    class _ApplyResult:
        def __init__(self, outcome: OperationOutcomeCategory) -> None:
            self.operation_outcome = outcome
            self.idempotent_replay = outcome is OperationOutcomeCategory.IDEMPOTENT_NOOP
            self.apply_outcome = BillingSubscriptionApplyOutcome.ACTIVE_APPLIED

    with (
        pytest.MonkeyPatch.context() as m,
        TestClient(app) as client,
    ):
        create_if_absent = AsyncMock()
        ingest = AsyncMock(return_value=_IngestResult())
        apply = AsyncMock(
            side_effect=[
                _ApplyResult(OperationOutcomeCategory.SUCCESS),
                _ApplyResult(OperationOutcomeCategory.IDEMPOTENT_NOOP),
            ]
        )
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", create_if_absent)
        m.setattr(ingress_mod.PostgresAtomicBillingIngestion, "ingest_normalized_billing_fact", ingest)
        m.setattr(ingress_mod.PostgresAtomicUC05SubscriptionApply, "apply_by_internal_fact_ref", apply)
        m.setattr(ingress_mod.PostgresSubscriptionSnapshotReader, "upsert_state", AsyncMock())
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        r1 = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
        r2 = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert create_if_absent.await_count == 2
    assert ingest.await_count == 2
    assert apply.await_count == 2


def test_valid_paid_event_persists_active_until_from_period_days() -> None:
    _now = datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC)
    app = create_payment_fulfillment_ingress_app(
        pool=object(), settings=_settings(strict_reference=True), now_utc_provider=lambda: _now
    )  # type: ignore[arg-type]
    payload = _payload_with_reference()
    payload["period_days"] = 10
    payload["paid_at"] = datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC).isoformat()
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings().secret, ts, body)

    class _IngestResult:
        class _Record:
            internal_fact_ref = "fact-1"

        record = _Record()

    class _ApplyResult:
        operation_outcome = OperationOutcomeCategory.SUCCESS
        idempotent_replay = False
        apply_outcome = BillingSubscriptionApplyOutcome.ACTIVE_APPLIED

    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", AsyncMock())
        m.setattr(
            ingress_mod.PostgresAtomicBillingIngestion,
            "ingest_normalized_billing_fact",
            AsyncMock(return_value=_IngestResult()),
        )
        m.setattr(
            ingress_mod.PostgresAtomicUC05SubscriptionApply,
            "apply_by_internal_fact_ref",
            AsyncMock(return_value=_ApplyResult()),
        )
        upsert_state = AsyncMock()
        m.setattr(ingress_mod.PostgresSubscriptionSnapshotReader, "upsert_state", upsert_state)
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        r = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert r.status_code == 200
    assert upsert_state.await_count == 1
    snapshot = upsert_state.await_args.args[0]
    assert snapshot.internal_user_id == "u12345"
    assert snapshot.state_label == "active"
    assert snapshot.active_until_utc == datetime(2026, 5, 7, 0, 0, 0, tzinfo=UTC)


def test_missing_payload_period_uses_default_period_without_rejection() -> None:
    _now = datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC)
    app = create_payment_fulfillment_ingress_app(
        pool=object(), settings=_settings(strict_reference=True), now_utc_provider=lambda: _now
    )  # type: ignore[arg-type]
    payload = _payload_with_reference()
    payload.pop("period_days")
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings().secret, ts, body)

    class _IngestResult:
        class _Record:
            internal_fact_ref = "fact-1"

        record = _Record()

    class _ApplyResult:
        operation_outcome = OperationOutcomeCategory.SUCCESS
        idempotent_replay = False
        apply_outcome = BillingSubscriptionApplyOutcome.ACTIVE_APPLIED

    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", AsyncMock())
        m.setattr(
            ingress_mod.PostgresAtomicBillingIngestion,
            "ingest_normalized_billing_fact",
            AsyncMock(return_value=_IngestResult()),
        )
        m.setattr(
            ingress_mod.PostgresAtomicUC05SubscriptionApply,
            "apply_by_internal_fact_ref",
            AsyncMock(return_value=_ApplyResult()),
        )
        m.setattr(ingress_mod.PostgresSubscriptionSnapshotReader, "upsert_state", AsyncMock())
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        r = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert r.status_code == 200


def test_invalid_period_rejects_without_mutation() -> None:
    app = create_payment_fulfillment_ingress_app(pool=object(), settings=_settings(strict_reference=True))  # type: ignore[arg-type]
    payload = _payload_with_reference()
    payload["period_days"] = -7
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings().secret, ts, body)
    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        create_if_absent = AsyncMock()
        ingest = AsyncMock()
        apply = AsyncMock()
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", create_if_absent)
        m.setattr(ingress_mod.PostgresAtomicBillingIngestion, "ingest_normalized_billing_fact", ingest)
        m.setattr(ingress_mod.PostgresAtomicUC05SubscriptionApply, "apply_by_internal_fact_ref", apply)
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        response = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert response.status_code == 400
    create_if_absent.assert_not_called()
    ingest.assert_not_called()
    apply.assert_not_called()


def test_strict_mode_rejects_expired_reference_without_mutation() -> None:
    app = create_payment_fulfillment_ingress_app(
        pool=object(),
        settings=_settings(strict_reference=True),
        now_utc_provider=lambda: datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC),
    )  # type: ignore[arg-type]
    payload = _payload_with_reference_issued_at(datetime(2026, 4, 19, 0, 0, 0, tzinfo=UTC))
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings(strict_reference=True).secret, ts, body)
    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        create_if_absent = AsyncMock()
        ingest = AsyncMock()
        apply = AsyncMock()
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", create_if_absent)
        m.setattr(ingress_mod.PostgresAtomicBillingIngestion, "ingest_normalized_billing_fact", ingest)
        m.setattr(ingress_mod.PostgresAtomicUC05SubscriptionApply, "apply_by_internal_fact_ref", apply)
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        response = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert response.status_code == 400
    create_if_absent.assert_not_called()
    ingest.assert_not_called()
    apply.assert_not_called()


def test_strict_mode_rejects_future_reference_without_mutation() -> None:
    app = create_payment_fulfillment_ingress_app(
        pool=object(),
        settings=_settings(strict_reference=True),
        now_utc_provider=lambda: datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC),
    )  # type: ignore[arg-type]
    payload = _payload_with_reference_issued_at(datetime(2026, 4, 27, 0, 20, 0, tzinfo=UTC))
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings(strict_reference=True).secret, ts, body)
    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        create_if_absent = AsyncMock()
        ingest = AsyncMock()
        apply = AsyncMock()
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", create_if_absent)
        m.setattr(ingress_mod.PostgresAtomicBillingIngestion, "ingest_normalized_billing_fact", ingest)
        m.setattr(ingress_mod.PostgresAtomicUC05SubscriptionApply, "apply_by_internal_fact_ref", apply)
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        response = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert response.status_code == 400
    create_if_absent.assert_not_called()
    ingest.assert_not_called()
    apply.assert_not_called()


def test_strict_mode_rejects_paid_at_before_checkout_reference_issued_at() -> None:
    app = create_payment_fulfillment_ingress_app(
        pool=object(),
        settings=_settings(strict_reference=True),
        now_utc_provider=lambda: datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC),
    )  # type: ignore[arg-type]
    payload = _payload_with_reference_issued_at(datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC))
    payload["paid_at"] = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat()
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings(strict_reference=True).secret, ts, body)
    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        create_if_absent = AsyncMock()
        ingest = AsyncMock()
        apply = AsyncMock()
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", create_if_absent)
        m.setattr(ingress_mod.PostgresAtomicBillingIngestion, "ingest_normalized_billing_fact", ingest)
        m.setattr(ingress_mod.PostgresAtomicUC05SubscriptionApply, "apply_by_internal_fact_ref", apply)
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        response = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert response.status_code == 400
    create_if_absent.assert_not_called()
    ingest.assert_not_called()
    apply.assert_not_called()


def test_strict_mode_rejects_missing_reference_without_mutation() -> None:
    app = create_payment_fulfillment_ingress_app(pool=object(), settings=_settings(strict_reference=True))  # type: ignore[arg-type]
    body = json.dumps(_payload()).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings(strict_reference=True).secret, ts, body)
    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        create_if_absent = AsyncMock()
        ingest = AsyncMock()
        apply = AsyncMock()
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", create_if_absent)
        m.setattr(ingress_mod.PostgresAtomicBillingIngestion, "ingest_normalized_billing_fact", ingest)
        m.setattr(ingress_mod.PostgresAtomicUC05SubscriptionApply, "apply_by_internal_fact_ref", apply)
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        response = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert response.status_code == 400
    create_if_absent.assert_not_called()
    ingest.assert_not_called()
    apply.assert_not_called()


def test_strict_mode_rejects_tampered_reference_without_mutation() -> None:
    app = create_payment_fulfillment_ingress_app(pool=object(), settings=_settings(strict_reference=True))  # type: ignore[arg-type]
    payload = _payload_with_reference()
    payload["client_reference_proof"] = "0" * 64
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings(strict_reference=True).secret, ts, body)
    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        create_if_absent = AsyncMock()
        ingest = AsyncMock()
        apply = AsyncMock()
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", create_if_absent)
        m.setattr(ingress_mod.PostgresAtomicBillingIngestion, "ingest_normalized_billing_fact", ingest)
        m.setattr(ingress_mod.PostgresAtomicUC05SubscriptionApply, "apply_by_internal_fact_ref", apply)
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        response = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert response.status_code == 400
    create_if_absent.assert_not_called()
    ingest.assert_not_called()
    apply.assert_not_called()


def test_proactive_success_notification_sent_once_on_first_apply() -> None:
    _now = datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC)
    notifier = AsyncMock()
    app = create_payment_fulfillment_ingress_app(
        pool=object(),
        settings=_settings(strict_reference=True),
        activation_telegram_notifier=notifier,
        now_utc_provider=lambda: _now,
    )  # type: ignore[arg-type]
    payload = _payload_with_reference()
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings(strict_reference=True).secret, ts, body)

    class _IngestResult:
        class _Record:
            internal_fact_ref = "fact-1"

        record = _Record()

    class _ApplyResult:
        operation_outcome = OperationOutcomeCategory.SUCCESS
        idempotent_replay = False
        apply_outcome = BillingSubscriptionApplyOutcome.ACTIVE_APPLIED

    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", AsyncMock())
        m.setattr(
            ingress_mod.PostgresAtomicBillingIngestion,
            "ingest_normalized_billing_fact",
            AsyncMock(return_value=_IngestResult()),
        )
        m.setattr(
            ingress_mod.PostgresAtomicUC05SubscriptionApply,
            "apply_by_internal_fact_ref",
            AsyncMock(return_value=_ApplyResult()),
        )
        m.setattr(ingress_mod.PostgresSubscriptionSnapshotReader, "upsert_state", AsyncMock())
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        r = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert r.status_code == 200
    notifier.send_subscription_activated_notice.assert_awaited_once()
    kwargs = notifier.send_subscription_activated_notice.await_args.kwargs
    text = kwargs["text"].lower()
    assert "оплата получена" in text
    assert "актив" in text
    assert kwargs["telegram_user_id"] == 12345
    assert kwargs["reply_markup"] is not None


def test_proactive_success_notification_not_sent_on_duplicate_apply() -> None:
    _now = datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC)
    notifier = AsyncMock()
    app = create_payment_fulfillment_ingress_app(
        pool=object(),
        settings=_settings(strict_reference=True),
        activation_telegram_notifier=notifier,
        now_utc_provider=lambda: _now,
    )  # type: ignore[arg-type]
    payload = _payload_with_reference()
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings(strict_reference=True).secret, ts, body)

    class _IngestResult:
        class _Record:
            internal_fact_ref = "fact-1"

        record = _Record()

    class _ApplyResult:
        def __init__(self, outcome: OperationOutcomeCategory) -> None:
            self.operation_outcome = outcome
            self.idempotent_replay = outcome is OperationOutcomeCategory.IDEMPOTENT_NOOP
            self.apply_outcome = BillingSubscriptionApplyOutcome.ACTIVE_APPLIED

    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", AsyncMock())
        m.setattr(
            ingress_mod.PostgresAtomicBillingIngestion,
            "ingest_normalized_billing_fact",
            AsyncMock(return_value=_IngestResult()),
        )
        m.setattr(
            ingress_mod.PostgresAtomicUC05SubscriptionApply,
            "apply_by_internal_fact_ref",
            AsyncMock(
                side_effect=[
                    _ApplyResult(OperationOutcomeCategory.SUCCESS),
                    _ApplyResult(OperationOutcomeCategory.IDEMPOTENT_NOOP),
                ]
            ),
        )
        m.setattr(ingress_mod.PostgresSubscriptionSnapshotReader, "upsert_state", AsyncMock())
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        r1 = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
        r2 = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert notifier.send_subscription_activated_notice.await_count == 1


def test_proactive_notification_not_sent_on_invalid_signature() -> None:
    notifier = AsyncMock()
    app = create_payment_fulfillment_ingress_app(
        pool=object(),
        settings=_settings(strict_reference=True),
        activation_telegram_notifier=notifier,
    )  # type: ignore[arg-type]
    body = json.dumps(_payload_with_reference()).encode("utf-8")
    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", AsyncMock())
        m.setattr(ingress_mod.PostgresAtomicBillingIngestion, "ingest_normalized_billing_fact", AsyncMock())
        m.setattr(ingress_mod.PostgresAtomicUC05SubscriptionApply, "apply_by_internal_fact_ref", AsyncMock())
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        r = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: "1777248000",
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + ("0" * 64),
            },
        )
    assert r.status_code == 401
    notifier.send_subscription_activated_notice.assert_not_called()


def test_proactive_notification_not_sent_when_apply_fails() -> None:
    _now = datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC)
    notifier = AsyncMock()
    app = create_payment_fulfillment_ingress_app(
        pool=object(),
        settings=_settings(strict_reference=True),
        activation_telegram_notifier=notifier,
        now_utc_provider=lambda: _now,
    )  # type: ignore[arg-type]
    payload = _payload_with_reference()
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings(strict_reference=True).secret, ts, body)

    class _IngestResult:
        class _Record:
            internal_fact_ref = "fact-1"

        record = _Record()

    class _ApplyFailed:
        operation_outcome = OperationOutcomeCategory.NOT_FOUND
        idempotent_replay = False
        apply_outcome = None

    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", AsyncMock())
        m.setattr(
            ingress_mod.PostgresAtomicBillingIngestion,
            "ingest_normalized_billing_fact",
            AsyncMock(return_value=_IngestResult()),
        )
        m.setattr(
            ingress_mod.PostgresAtomicUC05SubscriptionApply,
            "apply_by_internal_fact_ref",
            AsyncMock(return_value=_ApplyFailed()),
        )
        m.setattr(ingress_mod.PostgresSubscriptionSnapshotReader, "upsert_state", AsyncMock())
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        r = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert r.status_code == 409
    notifier.send_subscription_activated_notice.assert_not_called()


def test_proactive_notification_copy_has_no_sensitive_leaks() -> None:
    plan = build_fulfillment_success_notification_plan(
        correlation_id="fulfill-corr-1",
        active_until_ymd="2026-05-27",
    )
    text = render_telegram_outbound_plan(plan).message_text.lower()
    for needle in ("token", "secret", "reference", "signature"):
        assert needle not in text
    payload = _payload_with_reference()
    assert str(payload["external_payment_id"]).lower() not in text
    assert str(payload["external_event_id"]).lower() not in text


def test_strict_mode_rejects_telegram_user_mismatch_without_mutation() -> None:
    app = create_payment_fulfillment_ingress_app(pool=object(), settings=_settings(strict_reference=True))  # type: ignore[arg-type]
    payload = _payload_with_reference()
    payload["telegram_user_id"] = 54321
    body = json.dumps(payload).encode("utf-8")
    ts = "1777248000"
    sig = _sign(_settings(strict_reference=True).secret, ts, body)
    with pytest.MonkeyPatch.context() as m, TestClient(app) as client:
        create_if_absent = AsyncMock()
        ingest = AsyncMock()
        apply = AsyncMock()
        m.setattr(ingress_mod.PostgresUserIdentityRepository, "create_if_absent", create_if_absent)
        m.setattr(ingress_mod.PostgresAtomicBillingIngestion, "ingest_normalized_billing_fact", ingest)
        m.setattr(ingress_mod.PostgresAtomicUC05SubscriptionApply, "apply_by_internal_fact_ref", apply)
        m.setattr(ingress_mod.time, "time", lambda: 1777248000)
        response = client.post(
            "/billing/fulfillment/webhook",
            data=body,
            headers={
                ingress_mod.PAYMENT_TIMESTAMP_HEADER: ts,
                ingress_mod.PAYMENT_SIGNATURE_HEADER: "sha256=" + sig,
            },
        )
    assert response.status_code == 400
    create_if_absent.assert_not_called()
    ingest.assert_not_called()
    apply.assert_not_called()
