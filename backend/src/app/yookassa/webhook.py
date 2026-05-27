"""YooKassa webhook handler — receive payment notifications, verify via API, activate subscriptions."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
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

ENV_YOOKASSA_PROVIDER_KEY = "YOOKASSA_PROVIDER_KEY"

_DEFAULT_PROVIDER_KEY = "yookassa_v1"


def _get_provider_key() -> str:
    return os.environ.get(ENV_YOOKASSA_PROVIDER_KEY, _DEFAULT_PROVIDER_KEY).strip() or _DEFAULT_PROVIDER_KEY


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

        # Acknowledge all non-success events immediately
        if event != "payment.succeeded" or status != "succeeded":
            _LOGGER.info(
                "yookassa webhook: acknowledged event=%s status=%s id=%s",
                event, status, payment_id,
            )
            return JSONResponse({"ok": True, "acknowledged": True}, status_code=200)

        # --- payment.succeeded: verify via API before processing ---

        from app.yookassa.client import YooKassaClient

        client = YooKassaClient.from_env()
        if client is None:
            _LOGGER.error("yookassa webhook: client not configured, cannot verify payment")
            return _safe_json_error(503, "payment_provider_not_configured")

        verified = await client.get_payment(payment_id)
        if verified is None:
            _LOGGER.error("yookassa webhook: verification failed, payment not found id=%s", payment_id)
            return _safe_json_error(401, "verification_failed")

        if verified.status != "succeeded":
            _LOGGER.warning(
                "yookassa webhook: status mismatch webhook=%s api=%s id=%s",
                status, verified.status, payment_id,
            )
            return JSONResponse({"ok": True, "acknowledged": True}, status_code=200)

        # Use metadata from the verified API response (authoritative)
        metadata = verified.metadata
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
            _LOGGER.warning("yookassa webhook: missing or invalid telegram_user_id id=%s", payment_id)
            return _safe_json_error(400, "invalid_payload")

        from app.domain.plans import get_plan
        plan = get_plan(plan_id)
        if plan is None:
            _LOGGER.warning("yookassa webhook: unknown plan_id=%s id=%s", plan_id, payment_id)
            return _safe_json_error(400, "invalid_plan")

        amount_kopecks = None
        try:
            amount_kopecks = int(float(verified.amount_value) * 100)
        except (ValueError, TypeError):
            pass

        paid_at_str = payment_obj.get("captured_at") or payment_obj.get("created_at", "")
        try:
            if paid_at_str.endswith("Z"):
                paid_at_str = paid_at_str[:-1] + "+00:00"
            paid_at = datetime.fromisoformat(paid_at_str)
        except (ValueError, TypeError):
            paid_at = datetime.now(UTC)

        internal_user_id = f"u{telegram_user_id}"
        period_days = plan.duration_days

        _LOGGER.info(
            "yookassa webhook: verified and processing id=%s user=%s plan=%s amount=%s",
            payment_id, internal_user_id, plan_id, verified.amount_value,
        )

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
            _LOGGER.exception("yookassa webhook: fulfillment failed id=%s", payment_id)
            return _safe_json_error(503, "temporarily_unavailable")

        if result.operation_outcome not in (
            OperationOutcomeCategory.SUCCESS,
            OperationOutcomeCategory.IDEMPOTENT_NOOP,
        ):
            _LOGGER.warning(
                "yookassa webhook: fulfillment rejected outcome=%s id=%s",
                result.operation_outcome.value, payment_id,
            )
            return _safe_json_error(409, "rejected")

        _LOGGER.info(
            "yookassa webhook: payment processed id=%s user=%s outcome=%s",
            payment_id,
            internal_user_id,
            result.operation_outcome.value,
        )

        # Edit the original bot payment message to show success
        from app.bot_transport.payment_message_registry import pop_payment_message

        msg_ref = pop_payment_message(payment_id)
        if msg_ref is not None:
            chat_id, message_id = msg_ref

            active_until_str = "не указано"
            try:
                from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader

                snap = await PostgresSubscriptionSnapshotReader(pool).get_for_user(internal_user_id)
                if snap and snap.active_until_utc:
                    active_until_str = snap.active_until_utc.strftime("%d.%m.%Y")
            except Exception:
                pass
            success_text = (
                "✅ Оплата прошла успешно!\n\n"
                f"Ваша подписка активна до {active_until_str}.\n"
                "Приятного пользования!"
            )
            success_kb = {
                "inline_keyboard": [
                    [{"text": "📋 Моя подписка", "callback_data": "my_sub"}],
                    [{"text": "🔑 Мои ключи", "callback_data": "my_keys"}],
                ],
            }
            try:
                if activation_telegram_notifier is not None and hasattr(activation_telegram_notifier, "_client"):
                    await activation_telegram_notifier._client.edit_message_text(
                        chat_id, message_id, success_text, reply_markup=success_kb,
                    )
                else:
                    from app.runtime.telegram_httpx_raw_client import HttpxTelegramRawPollingClient

                    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or os.environ.get("BOT_TOKEN", "").strip()
                    if bot_token:
                        client_for_edit = HttpxTelegramRawPollingClient(bot_token)
                        await client_for_edit.edit_message_text(
                            chat_id, message_id, success_text, reply_markup=success_kb,
                        )
            except Exception:
                _LOGGER.debug("yookassa webhook: could not edit payment message id=%s", payment_id, exc_info=True)

        return JSONResponse({"ok": True, "accepted": True}, status_code=200)

    return handle_yookassa_webhook
