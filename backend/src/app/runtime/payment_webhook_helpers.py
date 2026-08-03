"""Shared payment-webhook helpers — provider-agnostic fulfillment logic.

Both the YooKassa webhook and the CryptoBot webhook funnel paid payments into
``process_fulfillment`` (UC-04 → UC-05 → snapshot → VLESS → referral → notify).
The provider-specific layer only differs in *how* it authenticates the webhook,
re-fetches the object, and extracts the payment context (metadata dict, paid
amount, paid_at, external id). Everything downstream is identical and lives here
as a single source of truth, so a subscription purchase or an add-device top-up
behaves the same regardless of provider — referral commissions, promo redemption
and amount anti-tamper included.

The payment context is normalized into a flat ``metadata: dict[str, str]`` with
the same keys YooKassa already uses (``plan_id, device_count, telegram_user_id,
internal_user_id, discount_kopecks, promo_code, kind, new_device_count,
expected_amount_kopecks``). CryptoBot encodes these into its single ``payload``
string and decodes them back into the same dict before calling these helpers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import asyncpg
from starlette.responses import JSONResponse

from app.runtime.fulfillment_processor import (
    FulfillmentActivationTelegramNotifier,
    FulfillmentInput,
    FulfillmentTelemetry,
    process_fulfillment,
)
from app.shared.types import OperationOutcomeCategory

_LOGGER = logging.getLogger(__name__)


def safe_json_error(status_code: int, error_code: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error_code}, status_code=status_code)


def amount_value_to_kopecks(amount_value: object) -> int | None:
    """Convert a provider money amount (string/Decimal/number) to integer kopecks.

    Uses ``Decimal`` to avoid float precision loss (e.g. ``"1099.99"`` must map
    to ``109999``, not a value off by one). Returns ``None`` when the value
    cannot be parsed, so callers fall through to their existing amount-mismatch
    / validation paths unchanged.
    """
    try:
        return int((Decimal(str(amount_value)) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        return None


def expected_subscription_kopecks(plan: Any, device_count: int, *, discount_kopecks: int = 0) -> int:
    """Kopecks a subscription purchase is expected to cost: plan price + extra devices − promo.

    The bot/web charge ``calculate_total_price(plan, device_count)`` at creation and apply the
    promo discount there, carrying ``discount_kopecks`` in the payment metadata. The webhook must
    expect the SAME discounted amount — subtracting ``discount_kopecks`` here keeps the amount
    gate in sync with what was charged (otherwise every promo payment → 409 amount_mismatch).
    Default 0 for legacy payments without the field (charged full price → unchanged behaviour).
    """
    from app.domain.plans import calculate_total_price

    return calculate_total_price(plan, device_count) * 100 - max(0, discount_kopecks)


def promo_from_metadata(metadata: dict[str, Any]) -> tuple[str, int] | None:
    """Промокод платежа из metadata: (code, discount_kopecks) или None, если кода нет.

    Безопасный parse как parse_add_device_metadata: default 0 для legacy-платежей без поля.
    """
    code = metadata.get("promo_code", "")
    if not isinstance(code, str) or not code:
        return None
    try:
        discount_kopecks = int(metadata.get("discount_kopecks", "0"))
    except (ValueError, TypeError):
        discount_kopecks = 0
    return code, max(0, discount_kopecks)


def parse_add_device_metadata(metadata: dict[str, str]) -> tuple[int, int, int] | None:
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


def validate_add_device_amount(expected_amount_kopecks: int, paid_kopecks: int | None) -> bool:
    """True when the paid amount matches the expected top-up cost (±1 kop, anti-tamper).

    Mirrors the subscription webhook's amount gate: the amount is fixed by the server
    when the payment is created, so a mismatch signals tampering or a misrouted payment.
    """
    if paid_kopecks is None or expected_amount_kopecks <= 0:
        return False
    return abs(paid_kopecks - expected_amount_kopecks) <= 1


def resolve_subscription_internal_user_id(metadata: dict[str, Any], telegram_user_id: int) -> str:
    """The internal_user_id to credit a paid subscription to.

    Prefers the id set at payment creation (``metadata.internal_user_id``) — correct
    for web-only (email) accounts, whose identity is ``web_<hash>`` rather than
    ``f"u{telegram_user_id}"``. Falls back to the Telegram convention for legacy /
    Telegram payments that don't carry the field. Pure (no I/O) → unit-tested directly.
    """
    return metadata.get("internal_user_id") or f"u{telegram_user_id}"


async def redeem_webhook_promo(
    *,
    pool: asyncpg.Pool,
    code: str,
    base_rubles: int,
    discounted_rubles: int,
    discount_kopecks: int,
    internal_user_id: str,
    payment_ref: str,
) -> None:
    """Best-effort погашение промокода при успехе оплаты (атомарно, идемпотентно).

    Срабатывает и при повторном вебхуке — UNIQUE(idempotency_key) гасит дубль без двойного +1.
    Деньги/fulfillment уже прошли, поэтому сбой погашения лишь логируется (аудит-пробел).
    """
    from app.application.promo_service import redeem_promo_on_success
    from app.persistence.postgres_promo import (
        PostgresPromoAppliedRepository,
        PostgresPromoCodeRepository,
        PostgresPromoRedemptionRepository,
    )

    await redeem_promo_on_success(
        pool=pool,
        promo_applied_repo=PostgresPromoAppliedRepository(pool),
        promo_code_repo=PostgresPromoCodeRepository(pool),
        promo_redemption_repo=PostgresPromoRedemptionRepository(pool),
        internal_user_id=internal_user_id,
        code=code,
        base_rubles=base_rubles,
        discounted_rubles=discounted_rubles,
        discount_kopecks=discount_kopecks,
        payment_ref=payment_ref,
    )


def build_telegram_edit_client_from_env() -> Any | None:
    """Lazily build the raw httpx Telegram client used to edit bot messages after payment.

    Provider-agnostic (only needs TELEGRAM_BOT_TOKEN/BOT_TOKEN). Returns None if no token
    is configured — the webhook then simply cannot edit the original prompt message, but the
    payment still succeeds (best-effort, never blocks fulfillment).
    """
    import os

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or os.environ.get("BOT_TOKEN", "").strip()
    if not bot_token:
        return None
    from app.runtime.telegram_httpx_raw_client import HttpxTelegramRawPollingClient

    return HttpxTelegramRawPollingClient(bot_token)


async def edit_payment_message_success(
    *,
    activation_telegram_notifier: FulfillmentActivationTelegramNotifier | None,
    payment_id: str,
    success_text: str,
    success_kb: dict[str, Any],
) -> None:
    """Edit the original bot payment prompt message to show success (best-effort).

    Shared by every provider. Looks up the (chat_id, message_id) the polling loop
    registered for this payment_id and edits it; failures are logged and swallowed
    (the payment still succeeded). ``payment_id`` is provider-scoped (yookassa id /
    cryptobot invoice id) — the bot registers the same id it received at creation.
    """
    from app.bot_transport.payment_message_registry import pop_payment_message

    msg_ref = pop_payment_message(payment_id)
    if msg_ref is None:
        return
    chat_id, message_id = msg_ref
    try:
        if activation_telegram_notifier is not None and hasattr(activation_telegram_notifier, "_client"):
            await activation_telegram_notifier._client.edit_message_text(
                chat_id,
                message_id,
                success_text,
                reply_markup=success_kb,
            )
        else:
            edit_client = build_telegram_edit_client_from_env()
            if edit_client is not None:
                await edit_client.edit_message_text(
                    chat_id,
                    message_id,
                    success_text,
                    reply_markup=success_kb,
                )
    except Exception:
        _LOGGER.debug("payment webhook: could not edit payment message id=%s", payment_id, exc_info=True)


async def apply_add_device_topup(
    *,
    pool: asyncpg.Pool,
    vless_provider: Any | None,
    activation_telegram_notifier: FulfillmentActivationTelegramNotifier | None,
    provider_key: str,
    external_event_id: str,
    payment_ref: str,
    metadata: dict[str, str],
    paid_kopecks: int | None,
    paid_at: datetime,
) -> JSONResponse:
    """Fulfill a kind=add_device payment: increase device_count, re-provision keys.

    Differs from a subscription purchase: the subscription duration is NOT
    extended, and device_count is raised (monotonically) rather than reset.
    Every side effect beyond the snapshot update is best-effort — the 30-min
    reprovision timer self-heals VLESS, and replays are idempotent because the
    device_count target is an absolute max and the ledger dedups by external_event_id.

    ``external_event_id`` is provider-scoped (e.g. ``cryptobot:<invoice_id>``);
    ``paid_kopecks`` is the paid RUB amount (used for anti-tamper + audit).
    """
    parsed = parse_add_device_metadata(metadata)
    if parsed is None:
        _LOGGER.warning("%s webhook: invalid add_device metadata ref=%s", provider_key, payment_ref)
        return safe_json_error(400, "invalid_payload")
    telegram_user_id, new_device_count, expected_amount_kopecks = parsed

    if not validate_add_device_amount(expected_amount_kopecks, paid_kopecks):
        _LOGGER.warning(
            "%s webhook: add_device amount mismatch expected=%s got=%s ref=%s",
            provider_key,
            expected_amount_kopecks,
            paid_kopecks,
            payment_ref,
        )
        return safe_json_error(409, "amount_mismatch")

    internal_user_id = f"u{telegram_user_id}"

    _LOGGER.info(
        "%s webhook: add_device verified ref=%s user=%s new_devices=%d",
        provider_key,
        payment_ref,
        internal_user_id,
        new_device_count,
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
            target,
            internal_user_id,
        )
    _LOGGER.info(
        "%s webhook: add_device applied ref=%s user=%s %d→%d",
        provider_key,
        payment_ref,
        internal_user_id,
        current,
        target,
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
                billing_provider_key=provider_key,
                external_event_id=external_event_id,
                event_type="device_topup",
                event_effective_at=paid_at,
                event_received_at=datetime.now(UTC),
                status=BillingEventLedgerStatus.ACCEPTED,
                ingestion_correlation_id=f"add-device-{payment_ref}",
                internal_user_id=internal_user_id,
                checkout_attempt_id=payment_ref,
                amount_currency=BillingEventAmountCurrency(amount_minor_units=paid_kopecks, currency_code="RUB"),
                internal_fact_ref=None,
            )
        )
    except Exception:
        _LOGGER.warning("%s webhook: add_device ledger audit failed ref=%s", provider_key, payment_ref, exc_info=True)

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
                "%s webhook: add_device vless reprovision failed ref=%s",
                provider_key,
                payment_ref,
                exc_info=True,
            )

    # Погашение промокода (best-effort, идемпотентно). add-device: base = discounted + discount
    # (expected_amount_kopecks уже = discounted стоимости доплаты за устройства).
    add_promo = promo_from_metadata(metadata)
    if add_promo is not None:
        await redeem_webhook_promo(
            pool=pool,
            code=add_promo[0],
            base_rubles=(expected_amount_kopecks + add_promo[1]) // 100,
            discounted_rubles=expected_amount_kopecks // 100,
            discount_kopecks=add_promo[1],
            internal_user_id=internal_user_id,
            payment_ref=payment_ref,
        )

    await edit_payment_message_success(
        activation_telegram_notifier=activation_telegram_notifier,
        payment_id=payment_ref,
        success_text=(
            f"✅ Оплата прошла успешно!\n\nКоличество устройств увеличено до {target}.\nПриятного пользования!"
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


async def fulfill_subscription_payment(
    *,
    pool: asyncpg.Pool,
    vless_provider: Any | None,
    activation_telegram_notifier: FulfillmentActivationTelegramNotifier | None,
    telemetry: FulfillmentTelemetry | None,
    provider_key: str,
    external_event_id: str,
    external_payment_id: str,
    metadata: dict[str, str],
    paid_amount_kopecks: int | None,
    paid_at: datetime,
    emit_activation_notice: bool = False,
) -> JSONResponse:
    """Fulfill a subscription-purchase payment: validate amount, apply, side effects.

    Shared provider-agnostic core for a ``subscription`` kind payment. Resolves
    plan/device/identity from ``metadata``, anti-tamper-checks the paid amount
    against the server-computed expected price (±1 kop; ``paid_amount_kopecks``
    may be ``None`` for providers whose RUB face value is fixed at creation and
    trusted via re-fetch), then runs ``process_fulfillment`` (UC-04 → UC-05 →
    snapshot → VLESS → referral commissions → notify) and redeems the promo.

    Returns the JSON webhook response. Caller has already authenticated the
    webhook and re-fetched the authoritative object.
    """
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
        _LOGGER.warning("%s webhook: missing or invalid telegram_user_id ref=%s", provider_key, external_payment_id)
        return safe_json_error(400, "invalid_payload")

    from app.domain.plans import get_plan

    plan = get_plan(plan_id)
    if plan is None:
        _LOGGER.warning("%s webhook: unknown plan_id=%s ref=%s", provider_key, plan_id, external_payment_id)
        return safe_json_error(400, "invalid_plan")

    # Expected amount must cover the plan PLUS any extra devices the user paid
    # for, MINUS the promo discount carried in metadata (default 0 for legacy
    # payments without the field). paid_amount_kopecks may be None for providers
    # that fix the RUB face value at creation (the re-fetch already proved 'paid').
    promo = promo_from_metadata(metadata)
    discount_kopecks = promo[1] if promo is not None else 0
    expected_kopecks = expected_subscription_kopecks(plan, device_count, discount_kopecks=discount_kopecks)
    if paid_amount_kopecks is not None and abs(paid_amount_kopecks - expected_kopecks) > 1:
        _LOGGER.warning(
            "%s webhook: amount mismatch expected=%d got=%d ref=%s",
            provider_key,
            expected_kopecks,
            paid_amount_kopecks,
            external_payment_id,
        )
        return safe_json_error(409, "amount_mismatch")
    amount_kopecks = expected_kopecks

    internal_user_id = resolve_subscription_internal_user_id(metadata, telegram_user_id)
    period_days = plan.duration_days

    _LOGGER.info(
        "%s webhook: verified and processing ref=%s user=%s plan=%s",
        provider_key,
        external_payment_id,
        internal_user_id,
        plan_id,
    )

    fulfillment_input = FulfillmentInput(
        provider_key=provider_key,
        external_event_id=external_event_id,
        external_payment_id=external_payment_id,
        telegram_user_id=telegram_user_id,
        internal_user_id=internal_user_id,
        paid_at=paid_at,
        period_days=period_days,
        amount_kopecks=amount_kopecks,
        device_count=device_count,
    )

    try:
        result = await process_fulfillment(
            pool=pool,
            inp=fulfillment_input,
            telemetry=telemetry,
            activation_telegram_notifier=activation_telegram_notifier,
            emit_activation_notice=emit_activation_notice,
            vless_provider=vless_provider,
        )
    except Exception:
        _LOGGER.exception("%s webhook: fulfillment failed ref=%s", provider_key, external_payment_id)
        return safe_json_error(503, "temporarily_unavailable")

    if result.operation_outcome not in (
        OperationOutcomeCategory.SUCCESS,
        OperationOutcomeCategory.IDEMPOTENT_NOOP,
    ):
        _LOGGER.warning(
            "%s webhook: fulfillment rejected outcome=%s ref=%s",
            provider_key,
            result.operation_outcome.value,
            external_payment_id,
        )
        return safe_json_error(409, "rejected")

    _LOGGER.info(
        "%s webhook: payment processed ref=%s user=%s outcome=%s",
        provider_key,
        external_payment_id,
        internal_user_id,
        result.operation_outcome.value,
    )

    # Погашение промокода (best-effort, идемпотентно). base/discounted пересчитываем из
    # канонической цены и discount_kopecks из metadata — те же значения, что при оплате.
    if promo is not None:
        from app.domain.plans import calculate_total_price as _calc_total

        base_rubles = _calc_total(plan, device_count)
        await redeem_webhook_promo(
            pool=pool,
            code=promo[0],
            base_rubles=base_rubles,
            discounted_rubles=max(0, base_rubles - promo[1] // 100),
            discount_kopecks=promo[1],
            internal_user_id=internal_user_id,
            payment_ref=external_payment_id,
        )

    # Edit the original bot payment message to show success
    active_until_str = "не указано"
    try:
        from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader

        snap = await PostgresSubscriptionSnapshotReader(pool).get_for_user(internal_user_id)
        if snap and snap.active_until_utc:
            active_until_str = snap.active_until_utc.strftime("%d.%m.%Y")
    except Exception:
        _LOGGER.debug("%s webhook: active_until lookup failed ref=%s", provider_key, external_payment_id, exc_info=True)
    await edit_payment_message_success(
        activation_telegram_notifier=activation_telegram_notifier,
        payment_id=external_payment_id,
        success_text=(
            f"✅ Оплата прошла успешно!\n\nВаша подписка активна до {active_until_str}.\nПриятного пользования!"
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
