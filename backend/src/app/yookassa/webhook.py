"""YooKassa webhook handler — receive payment notifications and activate subscriptions."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.runtime.fulfillment_processor import (
    FulfillmentActivationTelegramNotifier,
    FulfillmentInput,
    FulfillmentTelemetry,
    process_fulfillment,
)
from app.shared.types import OperationOutcomeCategory

_LOGGER = logging.getLogger(__name__)

ENV_YOOKASSA_WEBHOOK_SECRET = "YOOKASSA_WEBHOOK_SECRET"
ENV_YOOKASSA_PROVIDER_KEY = "YOOKASSA_PROVIDER_KEY"

_DEFAULT_PROVIDER_KEY = "yookassa_v1"


def _truthy(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in ("1", "true", "yes")


def _get_webhook_secret() -> str | None:
    return os.environ.get(ENV_YOOKASSA_WEBHOOK_SECRET, "").strip() or None


def _get_provider_key() -> str:
    return os.environ.get(ENV_YOOKASSA_PROVIDER_KEY, _DEFAULT_PROVIDER_KEY).strip() or _DEFAULT_PROVIDER_KEY


def _compute_signature(secret: str, raw_body: bytes) -> str:
    return hashlib.sha256(raw_body + secret.encode("utf-8")).hexdigest()


def _verify_signature(secret: str, signature_header: str, raw_body: bytes) -> bool:
    expected = _compute_signature(secret, raw_body)
    return hmac.compare_digest(signature_header.strip().lower(), expected.lower())


def _safe_json_error(status_code: int, error_code: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error_code}, status_code=status_code)


def create_yookassa_webhook_handler(
    *,
    pool: asyncpg.Pool,
    telemetry: FulfillmentTelemetry | None = None,
    activation_telegram_notifier: FulfillmentActivationTelegramNotifier | None = None,
    vless_provider: Any | None = None,
):
    """Returns a Starlette route handler for YooKassa webhook notifications."""

    async def handle_yookassa_webhook(request: Request) -> JSONResponse:
        raw_body = await request.body()

        secret = _get_webhook_secret()
        if secret:
            sig_header = request.headers.get("X-Request-Signature", "")
            if not sig_header or not _verify_signature(secret, sig_header, raw_body):
                _LOGGER.warning("yookassa webhook: invalid signature")
                return _safe_json_error(401, "unauthorized")

        try:
            notification = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            _LOGGER.warning("yookassa webhook: invalid JSON")
            return _safe_json_error(400, "invalid_payload")

        event = notification.get("event", "")
        payment_obj = notification.get("object", {})

        if not isinstance(payment_obj, dict):
            _LOGGER.warning("yookassa webhook: payment object is not a dict")
            return _safe_json_error(400, "invalid_payload")

        payment_id = payment_obj.get("id", "")
        status = payment_obj.get("status", "")

        if event != "payment.succeeded" or status != "succeeded":
            _LOGGER.info("yookassa webhook: ignoring event=%s status=%s", event, status)
            return JSONResponse({"ok": True, "ignored": True}, status_code=200)

        metadata = payment_obj.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        plan_id = metadata.get("plan_id", "")
        device_count_raw = metadata.get("device_count", "5")
        try:
            device_count = int(device_count_raw)
        except (ValueError, TypeError):
            device_count = 5

        telegram_user_id_raw = metadata.get("telegram_user_id", "")
        try:
            telegram_user_id = int(telegram_user_id_raw)
        except (ValueError, TypeError):
            _LOGGER.warning("yookassa webhook: missing or invalid telegram_user_id")
            return _safe_json_error(400, "invalid_payload")

        from app.domain.plans import get_plan
        plan = get_plan(plan_id)
        if plan is None:
            _LOGGER.warning("yookassa webhook: unknown plan_id=%s", plan_id)
            return _safe_json_error(400, "invalid_plan")

        amount_obj = payment_obj.get("amount", {})
        amount_value_raw = amount_obj.get("value", "0")
        try:
            amount_kopecks = int(float(amount_value_raw) * 100)
        except (ValueError, TypeError):
            amount_kopecks = None

        paid_at_str = payment_obj.get("captured_at") or payment_obj.get("created_at", "")
        from datetime import UTC, datetime
        try:
            if paid_at_str.endswith("Z"):
                paid_at_str = paid_at_str[:-1] + "+00:00"
            paid_at = datetime.fromisoformat(paid_at_str)
        except (ValueError, TypeError):
            paid_at = datetime.now(UTC)

        internal_user_id = f"u{telegram_user_id}"
        period_days = plan.duration_days

        fulfillment_input = FulfillmentInput(
            provider_key=_get_provider_key(),
            external_event_id=f"yookassa:{payment_id}",
            external_payment_id=payment_id,
            telegram_user_id=telegram_user_id,
            internal_user_id=internal_user_id,
            paid_at=paid_at,
            period_days=period_days,
            amount_kopecks=amount_kopecks,
        )

        try:
            result = await process_fulfillment(
                pool=pool,
                inp=fulfillment_input,
                telemetry=telemetry,
                activation_telegram_notifier=activation_telegram_notifier,
                vless_provider=vless_provider,
            )
        except Exception:
            _LOGGER.exception("yookassa webhook: fulfillment failed")
            return _safe_json_error(503, "temporarily_unavailable")

        if result.operation_outcome not in (
            OperationOutcomeCategory.SUCCESS,
            OperationOutcomeCategory.IDEMPOTENT_NOOP,
        ):
            _LOGGER.warning(
                "yookassa webhook: fulfillment rejected outcome=%s",
                result.operation_outcome.value,
            )
            return _safe_json_error(409, "rejected")

        _LOGGER.info(
            "yookassa webhook: payment processed id=%s user=%s",
            payment_id,
            internal_user_id,
        )
        return JSONResponse({"ok": True, "accepted": True}, status_code=200)

    return handle_yookassa_webhook
