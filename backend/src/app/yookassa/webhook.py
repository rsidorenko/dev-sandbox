"""YooKassa webhook handler — receive payment notifications, verify via API, activate subscriptions.

Provider-specific layer only: source-IP verification (YooKassa does not send an HMAC),
the YooKassa API client, and extraction of the payment context. All fulfillment logic
(UC-04 → UC-05 → snapshot → VLESS → referral → notify, amount anti-tamper, promo
redemption, add-device top-up) lives in the shared ``payment_webhook_helpers`` module
and is reused verbatim by the CryptoBot webhook.
"""

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
    FulfillmentTelemetry,
)
from app.runtime.payment_webhook_helpers import (
    amount_value_to_kopecks as _amount_value_to_kopecks,
)
from app.runtime.payment_webhook_helpers import (
    apply_add_device_topup,
    fulfill_subscription_payment,
)
from app.runtime.payment_webhook_helpers import (
    safe_json_error as _safe_json_error,
)

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
        # PD-audit: сам IP в лог не пишется (не персональные данные не собираем) —
        # диапазонная проверка в памяти остаётся, решение reject/proceed то же.
        _LOGGER.warning("yookassa webhook: source not in YooKassa ranges — rejected")
        return _safe_json_error(401, "invalid_source")
    _LOGGER.warning("yookassa webhook: no forwarded client IP — relying on API re-fetch")
    return None


def _get_provider_key() -> str:
    return os.environ.get(ENV_YOOKASSA_PROVIDER_KEY, _DEFAULT_PROVIDER_KEY).strip() or _DEFAULT_PROVIDER_KEY


def _parse_paid_at(payment_obj: dict[str, Any]) -> datetime:
    paid_at_str = payment_obj.get("captured_at") or payment_obj.get("created_at", "")
    try:
        if paid_at_str.endswith("Z"):
            paid_at_str = paid_at_str[:-1] + "+00:00"
        return datetime.fromisoformat(paid_at_str)
    except (ValueError, TypeError):
        return datetime.now(UTC)


def create_yookassa_webhook_handler(
    *,
    pool: asyncpg.Pool,
    telemetry: FulfillmentTelemetry | None = None,
    activation_telegram_notifier: FulfillmentActivationTelegramNotifier | None = None,
    vless_provider: Any | None = None,
):
    """Returns a Starlette route handler for YooKassa webhook notifications."""

    _yookassa_client: Any | None = None

    def _get_yookassa_client() -> Any | None:
        nonlocal _yookassa_client
        if _yookassa_client is None:
            from app.yookassa.client import YooKassaClient

            _yookassa_client = YooKassaClient.from_env()
        return _yookassa_client

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
                event,
                status,
                payment_id,
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
                status,
                verified.status,
                payment_id,
            )
            return JSONResponse({"ok": True, "acknowledged": True}, status_code=200)

        # Use metadata from the verified API response (authoritative)
        metadata = verified.metadata

        provider_key = _get_provider_key()
        external_event_id = f"yookassa:{payment_id}"
        paid_kopecks = _amount_value_to_kopecks(verified.amount_value)
        paid_at = _parse_paid_at(payment_obj)

        # Device top-up payments (kind=add_device) are fulfilled by a dedicated
        # path that increases device_count without extending the subscription —
        # they must NOT fall through to the subscription plan/amount logic below
        # (their amount is a top-up cost, not a plan price).
        if metadata.get("kind", "subscription") == "add_device":
            return await apply_add_device_topup(
                pool=pool,
                vless_provider=vless_provider,
                activation_telegram_notifier=activation_telegram_notifier,
                provider_key=provider_key,
                external_event_id=external_event_id,
                payment_ref=payment_id,
                metadata=metadata,
                paid_kopecks=paid_kopecks,
                paid_at=paid_at,
            )

        return await fulfill_subscription_payment(
            pool=pool,
            vless_provider=vless_provider,
            activation_telegram_notifier=activation_telegram_notifier,
            telemetry=telemetry,
            provider_key=provider_key,
            external_event_id=external_event_id,
            external_payment_id=payment_id,
            metadata=metadata,
            paid_amount_kopecks=paid_kopecks,
            paid_at=paid_at,
            emit_activation_notice=False,
        )

    return handle_yookassa_webhook
