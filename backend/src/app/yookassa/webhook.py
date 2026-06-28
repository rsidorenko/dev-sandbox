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


def _parse_add_device_metadata(metadata: dict[str, str]) -> tuple[int, int, int] | None:
    """Parse kind=add_device payment metadata.

    Returns ``(telegram_user_id, new_device_count, expected_amount_kopecks)`` or
    ``None`` when any required field is missing/invalid or new_device_count is out
    of the supported 1..20 range. Pure (no I/O) so it can be unit-tested directly.
    """
    try:
        telegram_user_id = int(metadata.get("telegram_user_id", ""))
        new_device_count = int(metadata.get("new_device_count", "0"))
        expected_amount_kopecks = int(metadata.get("expected_amount_kopecks", "0"))
    except (ValueError, TypeError):
        return None
    if telegram_user_id <= 0:
        return None
    if new_device_count < 1 or new_device_count > 20:
        return None
    if expected_amount_kopecks <= 0:
        return None
    return telegram_user_id, new_device_count, expected_amount_kopecks


def _validate_add_device_amount(expected_amount_kopecks: int, paid_kopecks: int | None) -> bool:
    """True when the paid amount matches the expected top-up cost (±1 kop, anti-tamper).

    Mirrors the subscription webhook's amount gate: the amount is fixed by the server
    when the payment is created, so a mismatch signals tampering or a misrouted payment.
    """
    if paid_kopecks is None or expected_amount_kopecks <= 0:
        return False
    return abs(paid_kopecks - expected_amount_kopecks) <= 1


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

    async def _edit_payment_message_success(payment_id: str, *, success_text: str, success_kb: dict[str, Any]) -> None:
        """Edit the original bot payment prompt message to show success (best-effort).

        Shared by the subscription and add-device payment paths. Looks up the
        (chat_id, message_id) the polling loop registered for this payment_id and
        edits it; failures are logged and swallowed (the payment still succeeded).
        """
        from app.bot_transport.payment_message_registry import pop_payment_message

        msg_ref = pop_payment_message(payment_id)
        if msg_ref is None:
            return
        chat_id, message_id = msg_ref
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

    async def _apply_add_device_topup(
        *,
        payment_id: str,
        payment_obj: dict[str, Any],
        verified: Any,
        metadata: dict[str, str],
    ) -> JSONResponse:
        """Fulfill a kind=add_device payment: increase device_count, re-provision keys.

        Differs from a subscription purchase: the subscription duration is NOT
        extended, and device_count is raised (monotonically) rather than reset.
        Every side effect beyond the snapshot update is best-effort — the 30-min
        reprovision timer self-heals VLESS, and replays are idempotent because the
        device_count target is an absolute max and the ledger dedups by payment_id.
        """
        parsed = _parse_add_device_metadata(metadata)
        if parsed is None:
            _LOGGER.warning("yookassa webhook: invalid add_device metadata id=%s", payment_id)
            return _safe_json_error(400, "invalid_payload")
        telegram_user_id, new_device_count, expected_amount_kopecks = parsed

        paid_kopecks: int | None
        try:
            paid_kopecks = round(float(verified.amount_value) * 100)
        except (ValueError, TypeError):
            paid_kopecks = None
        if not _validate_add_device_amount(expected_amount_kopecks, paid_kopecks):
            _LOGGER.warning(
                "yookassa webhook: add_device amount mismatch expected=%s got=%s id=%s",
                expected_amount_kopecks, paid_kopecks, payment_id,
            )
            return _safe_json_error(409, "amount_mismatch")

        paid_at_str = payment_obj.get("captured_at") or payment_obj.get("created_at", "")
        try:
            if paid_at_str.endswith("Z"):
                paid_at_str = paid_at_str[:-1] + "+00:00"
            paid_at = datetime.fromisoformat(paid_at_str)
        except (ValueError, TypeError):
            paid_at = datetime.now(UTC)

        internal_user_id = f"u{telegram_user_id}"

        _LOGGER.info(
            "yookassa webhook: add_device verified id=%s user=%s new_devices=%d amount=%s",
            payment_id, internal_user_id, new_device_count, verified.amount_value,
        )

        # Apply the device-count increase. `max()` makes this monotonic and
        # idempotent: a replayed webhook or a race with a balance top-up can never
        # reduce the count, and re-applying the same target is a no-op.
        snap_row = await pool.fetchrow(
            "SELECT device_count, active_until_utc FROM subscription_snapshots WHERE internal_user_id = $1",
            internal_user_id,
        )
        current = (snap_row["device_count"] if snap_row else 0) or 0
        target = max(current, new_device_count)
        if target > current:
            await pool.execute(
                "UPDATE subscription_snapshots SET device_count = $1, updated_at = NOW() WHERE internal_user_id = $2",
                target, internal_user_id,
            )
        _LOGGER.info(
            "yookassa webhook: add_device applied id=%s user=%s %d→%d",
            payment_id, internal_user_id, current, target,
        )

        # Best-effort audit in the billing ledger. event_type="device_topup" is NOT
        # in the UC-05 apply allowlist, so this records the paid fact only — no
        # subscription is (re)activated. Dedup by external_event_id keeps replays
        # idempotent. Runs with its own connection/transaction, isolated from the
        # snapshot update above so an audit failure can never roll it back.
        try:
            from app.application.billing_ingestion import NormalizedBillingFactInput
            from app.persistence.billing_events_ledger_contracts import (
                BillingEventAmountCurrency,
                BillingEventLedgerStatus,
            )
            from app.persistence.postgres_billing_ingestion_atomic import PostgresAtomicBillingIngestion
            from app.persistence.postgres_user_identity import PostgresUserIdentityRepository

            await PostgresUserIdentityRepository(pool).create_if_absent(telegram_user_id)
            await PostgresAtomicBillingIngestion(pool).ingest_normalized_billing_fact(
                NormalizedBillingFactInput(
                    billing_provider_key=_get_provider_key(),
                    external_event_id=f"yookassa:{payment_id}",
                    event_type="device_topup",
                    event_effective_at=paid_at,
                    event_received_at=datetime.now(UTC),
                    status=BillingEventLedgerStatus.ACCEPTED,
                    ingestion_correlation_id=f"add-device-{payment_id}",
                    internal_user_id=internal_user_id,
                    checkout_attempt_id=payment_id,
                    amount_currency=BillingEventAmountCurrency(
                        amount_minor_units=paid_kopecks, currency_code="RUB"
                    ),
                    internal_fact_ref=None,
                )
            )
        except Exception:
            _LOGGER.warning(
                "yookassa webhook: add_device ledger audit failed id=%s", payment_id, exc_info=True
            )

        # Best-effort VLESS re-provision with the new limitIp. Preserve the existing
        # expiry: compute days_left from active_until_utc (like reconcile_all_users)
        # instead of activate_user's 365d default, which would wrongly extend the
        # subscription. The reprovision timer self-heals within 30 min on failure.
        if vless_provider is not None and snap_row is not None:
            try:
                active_until = snap_row["active_until_utc"]
                now = datetime.now(UTC)
                if active_until is not None and active_until > now:
                    days_left = max(1, (active_until - now).days)
                else:
                    days_left = 30
                await vless_provider.activate_user(
                    internal_user_id=internal_user_id,
                    device_count=target,
                    expiry_days=days_left,
                )
            except Exception:
                _LOGGER.warning(
                    "yookassa webhook: add_device vless reprovision failed id=%s",
                    payment_id, exc_info=True,
                )

        await _edit_payment_message_success(
            payment_id,
            success_text=(
                "✅ Оплата прошла успешно!\n\n"
                f"Количество устройств увеличено до {target}.\n"
                "Приятного пользования!"
            ),
            success_kb={
                "inline_keyboard": [
                    [
                        {"text": "📋 Меню", "callback_data": "main_menu"},
                        {"text": "🔑 Мои ключи", "callback_data": "my_keys"},
                    ],
                ],
            },
        )

        return JSONResponse({"ok": True, "accepted": True}, status_code=200)

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

        # Device top-up payments (kind=add_device) are fulfilled by a dedicated
        # path that increases device_count without extending the subscription —
        # they must NOT fall through to the subscription plan/amount logic below
        # (their amount is a top-up cost, not a plan price).
        if metadata.get("kind", "subscription") == "add_device":
            return await _apply_add_device_topup(
                payment_id=payment_id,
                payment_obj=payment_obj,
                verified=verified,
                metadata=metadata,
            )

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
        active_until_str = "не указано"
        try:
            from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader

            snap = await PostgresSubscriptionSnapshotReader(pool).get_for_user(internal_user_id)
            if snap and snap.active_until_utc:
                active_until_str = snap.active_until_utc.strftime("%d.%m.%Y")
        except Exception:
            pass
        await _edit_payment_message_success(
            payment_id,
            success_text=(
                "✅ Оплата прошла успешно!\n\n"
                f"Ваша подписка активна до {active_until_str}.\n"
                "Приятного пользования!"
            ),
            success_kb={
                "inline_keyboard": [
                    [
                        {"text": "📋 Меню", "callback_data": "main_menu"},
                        {"text": "🔑 Мои ключи", "callback_data": "my_keys"},
                    ],
                ],
            },
        )

        return JSONResponse({"ok": True, "accepted": True}, status_code=200)

    return handle_yookassa_webhook
