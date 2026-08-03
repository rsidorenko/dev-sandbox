"""CryptoBot webhook handler — verify HMAC signature, re-fetch invoice, fulfill.

Provider-specific layer only: CryptoBot signs webhooks with an HMAC-SHA256 header
(unlike YooKassa's IP auth), and the payment context rides in the invoice's
``payload`` string. After signature verification + an authoritative ``getInvoices``
re-fetch, the paid invoice is normalized into the same ``metadata: dict[str, str]``
shape YooKassa produces and handed to the shared fulfillment helpers — so a crypto
payment activates the subscription, provisions VLESS keys, credits referral
commissions and redeems promo codes identically to a card payment.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.cryptobot.payload import decode_payment_context
from app.runtime.fulfillment_processor import (
    FulfillmentActivationTelegramNotifier,
    FulfillmentTelemetry,
)
from app.runtime.payment_webhook_helpers import (
    amount_value_to_kopecks,
    apply_add_device_topup,
    fulfill_subscription_payment,
    safe_json_error,
)

_LOGGER = logging.getLogger(__name__)

ENV_CRYPTOBOT_PROVIDER_KEY = "CRYPTOBOT_PROVIDER_KEY"
ENV_CRYPTOBOT_API_TOKEN = "CRYPTOBOT_API_TOKEN"
_DEFAULT_PROVIDER_KEY = "cryptobot_v1"

_SIGNATURE_HEADER = "crypto-pay-api-signature"


def verify_cryptobot_signature(*, api_token: str, raw_body: bytes, signature_header: str | None) -> bool:
    """Verify the ``crypto-pay-api-signature`` header (HMAC-SHA256 over the raw body).

    Per Crypto Pay API docs the HMAC key is the SHA-256 digest of the app API token.
    Constant-time comparison; fails closed (``False``) on any missing/invalid input.
    """
    if not api_token or not signature_header:
        return False
    secret = hashlib.sha256(api_token.encode("utf-8")).digest()
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _parse_paid_at(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        return datetime.now(UTC)
    try:
        iso = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(iso)
    except ValueError:
        return datetime.now(UTC)


def _get_provider_key() -> str:
    return os.environ.get(ENV_CRYPTOBOT_PROVIDER_KEY, _DEFAULT_PROVIDER_KEY).strip() or _DEFAULT_PROVIDER_KEY


def create_cryptobot_webhook_handler(
    *,
    pool: asyncpg.Pool,
    telemetry: FulfillmentTelemetry | None = None,
    activation_telegram_notifier: FulfillmentActivationTelegramNotifier | None = None,
    vless_provider: Any | None = None,
):
    """Returns a Starlette route handler for CryptoBot webhook updates."""

    _cryptobot_client: Any | None = None

    def _get_cryptobot_client() -> Any | None:
        nonlocal _cryptobot_client
        if _cryptobot_client is None:
            from app.cryptobot.client import CryptoBotClient

            _cryptobot_client = CryptoBotClient.from_env()
        return _cryptobot_client

    async def handle_cryptobot_webhook(request: Request) -> JSONResponse:
        raw_body = await request.body()

        # ── Auth layer 1: HMAC signature over the raw body (fail-closed). ──
        api_token = os.environ.get(ENV_CRYPTOBOT_API_TOKEN, "").strip()
        if not api_token:
            _LOGGER.error("cryptobot webhook: CRYPTOBOT_API_TOKEN not configured")
            return safe_json_error(503, "payment_provider_not_configured")
        if not verify_cryptobot_signature(
            api_token=api_token,
            raw_body=raw_body,
            signature_header=request.headers.get(_SIGNATURE_HEADER),
        ):
            _LOGGER.warning("cryptobot webhook: invalid signature")
            return safe_json_error(401, "invalid_signature")

        try:
            update = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            _LOGGER.warning("cryptobot webhook: invalid JSON")
            return safe_json_error(400, "invalid_payload")

        update_type = update.get("update_type", "")
        invoice_obj = update.get("payload", {})
        if not isinstance(invoice_obj, dict):
            _LOGGER.warning("cryptobot webhook: payload is not a dict")
            return safe_json_error(400, "invalid_payload")

        # Only paid invoices trigger fulfillment; ack everything else.
        if update_type != "invoice_paid":
            _LOGGER.info("cryptobot webhook: acknowledged update_type=%s", update_type)
            return JSONResponse({"ok": True, "acknowledged": True}, status_code=200)

        invoice_id = str(invoice_obj.get("invoice_id", ""))
        if not invoice_id:
            _LOGGER.warning("cryptobot webhook: missing invoice_id")
            return safe_json_error(400, "invalid_payload")

        # ── Auth layer 2: authoritative re-fetch (object-status auth). ──
        client = _get_cryptobot_client()
        if client is None:
            _LOGGER.error("cryptobot webhook: client not configured, cannot verify invoice")
            return safe_json_error(503, "payment_provider_not_configured")

        verified = await client.get_invoice(invoice_id)
        if verified is None:
            _LOGGER.error("cryptobot webhook: verification failed, invoice not found id=%s", invoice_id)
            return safe_json_error(401, "verification_failed")

        if verified.status != "paid":
            _LOGGER.warning("cryptobot webhook: status not paid api=%s id=%s", verified.status, invoice_id)
            return JSONResponse({"ok": True, "acknowledged": True}, status_code=200)

        # Our payment context, round-tripped through the invoice payload string.
        metadata = decode_payment_context(verified.payload)

        provider_key = _get_provider_key()
        external_event_id = f"cryptobot:{invoice_id}"
        # CryptoBot fiat invoice: the RUB face value is what we fixed at creation,
        # so it equals the expected price (anti-tamper anchor, no crypto amount involved).
        paid_amount_kopecks = amount_value_to_kopecks(verified.fiat_amount)
        paid_at = _parse_paid_at(invoice_obj.get("paid_at"))

        # add_device top-ups take a dedicated path (device_count bump, no renewal).
        if metadata.get("kind", "subscription") == "add_device":
            return await apply_add_device_topup(
                pool=pool,
                vless_provider=vless_provider,
                activation_telegram_notifier=activation_telegram_notifier,
                provider_key=provider_key,
                external_event_id=external_event_id,
                payment_ref=invoice_id,
                metadata=metadata,
                paid_kopecks=paid_amount_kopecks,
                paid_at=paid_at,
            )

        return await fulfill_subscription_payment(
            pool=pool,
            vless_provider=vless_provider,
            activation_telegram_notifier=activation_telegram_notifier,
            telemetry=telemetry,
            provider_key=provider_key,
            external_event_id=external_event_id,
            external_payment_id=invoice_id,
            metadata=metadata,
            paid_amount_kopecks=paid_amount_kopecks,
            paid_at=paid_at,
            emit_activation_notice=False,
        )

    return handle_cryptobot_webhook
