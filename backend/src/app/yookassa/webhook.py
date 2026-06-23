"""YooKassa webhook handler — receive payment notifications, verify via API, activate subscriptions."""

from __future__ import annotations

import ipaddress
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

# YooKassa notification source IP ranges (Notification authentication — IP auth).
# YooKassa does NOT send an HMAC signature header; per its docs, authenticity is
# checked by source IP and/or by re-fetching the object. We do both: the IP check
# here + the authoritative client.get_payment() re-fetch downstream (object-status
# auth). The previous HMAC-signature check rejected every real notification
# (401 'missing signature header') and broke ALL YooKassa payments.
# https://yookassa.ru/developers/using-api/webhooks
_YOOKASSA_IP_NETWORKS = (
    ipaddress.ip_network("185.71.76.0/27"),
    ipaddress.ip_network("185.71.77.0/27"),
    ipaddress.ip_network("77.75.153.0/25"),
    ipaddress.ip_network("77.75.156.11/32"),
    ipaddress.ip_network("77.75.156.35/32"),
    ipaddress.ip_network("77.75.154.128/25"),
    ipaddress.ip_network("2a02:5180::/32"),
)


def _client_ip(request: Request) -> str | None:
    """The real sender IP. Behind nginx the connection host is the proxy, so prefer
    X-Forwarded-For / X-Real-IP (set by nginx to the original client = YooKassa).
    X-Forwarded-For may be a chain; the leftmost entry is the original client."""
    for header in ("x-forwarded-for", "x-real-ip"):
        value = request.headers.get(header, "").strip()
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else None


def _is_yookassa_ip(ip_str: str) -> bool:
    """True if *ip_str* is in one of YooKassa's documented notification ranges."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in _YOOKASSA_IP_NETWORKS)


def _verify_yookassa_source(request: Request) -> JSONResponse | None:
    """Verify the notification comes from YooKassa (IP auth, per YooKassa docs).
    Returns an error response to reject, or None to proceed. Authenticity is also
    enforced authoritatively downstream by re-fetching the payment via the YooKassa
    API (object-status auth) — so if no forwarded client IP is available we still
    proceed and rely on that re-fetch rather than rejecting (which would break
    deployments where the reverse proxy does not set X-Forwarded-For)."""
    ip = _client_ip(request)
    has_forwarded = bool(request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"))
    if ip is not None and _is_yookassa_ip(ip):
        return None  # verified YooKassa source
    if has_forwarded:
        _LOGGER.warning("yookassa webhook: source IP %s not in YooKassa ranges — rejected", ip)
        return _safe_json_error(401, "invalid_source")
    _LOGGER.warning("yookassa webhook: no forwarded client IP (%s) — relying on API re-fetch", ip)
    return None


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

    _yookassa_client: Any | None = None
    _telegram_edit_client: Any | None = None

    def _get_yookassa_client() -> Any | None:
        nonlocal _yookassa_client
        if _yookassa_client is None:
            from app.yookassa.client import YooKassaClient
            _yookassa_client = YooKassaClient.from_env()
        return _yookassa_client

    def _get_telegram_edit_client() -> Any | None:
        nonlocal _telegram_edit_client
        if _telegram_edit_client is None:
            bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or os.environ.get("BOT_TOKEN", "").strip()
            if bot_token:
                from app.runtime.telegram_httpx_raw_client import HttpxTelegramRawPollingClient
                _telegram_edit_client = HttpxTelegramRawPollingClient(bot_token)
        return _telegram_edit_client

    async def handle_yookassa_webhook(request: Request) -> JSONResponse:
        src_err = _verify_yookassa_source(request)
        if src_err is not None:
            return src_err

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

        client = _get_yookassa_client()
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
            amount_kopecks = round(float(verified.amount_value) * 100)
        except (ValueError, TypeError):
            pass

        expected_kopecks = plan.price_rubles * 100
        if amount_kopecks is not None and abs(amount_kopecks - expected_kopecks) > 1:
            _LOGGER.warning(
                "yookassa webhook: amount mismatch expected=%d got=%d id=%s",
                expected_kopecks, amount_kopecks, payment_id,
            )
            return _safe_json_error(409, "amount_mismatch")
        amount_kopecks = expected_kopecks

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
                activation_telegram_notifier=None,
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
                    [
                        {"text": "📋 Меню", "callback_data": "main_menu"},
                        {"text": "🔑 Мои ключи", "callback_data": "my_keys"},
                    ],
                ],
            }
            try:
                if activation_telegram_notifier is not None and hasattr(activation_telegram_notifier, "_client"):
                    await activation_telegram_notifier._client.edit_message_text(
                        chat_id, message_id, success_text, reply_markup=success_kb,
                    )
                else:
                    edit_client = _get_telegram_edit_client()
                    if edit_client is not None:
                        await edit_client.edit_message_text(
                            chat_id, message_id, success_text, reply_markup=success_kb,
                        )
            except Exception:
                _LOGGER.debug("yookassa webhook: could not edit payment message id=%s", payment_id, exc_info=True)

        return JSONResponse({"ok": True, "accepted": True}, status_code=200)

    return handle_yookassa_webhook
