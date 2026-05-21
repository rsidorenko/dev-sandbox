"""Postgres customer journey e2e smoke: storefront -> signed fulfillment -> active access journey."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

import asyncpg
import httpx

from app.admin_support.adm01_identity_resolve_adapter import Adm01IdentityResolveAdapter
from app.admin_support.adm01_postgres_issuance_read_adapter import Adm01PostgresIssuanceReadAdapter
from app.admin_support.adm01_postgres_subscription_read_adapter import Adm01PostgresSubscriptionReadAdapter
from app.admin_support.adm02_ensure_access_endpoint import (
    Adm02EnsureAccessInboundRequest,
    execute_adm02_ensure_access_endpoint,
)
from app.admin_support.adm02_ensure_access_mutation import Adm02EnsureAccessIssuanceMutationAdapter
from app.admin_support.adm02_wiring import build_adm02_ensure_access_handler
from app.admin_support.principal_extraction import DefaultInternalAdminPrincipalExtractor
from app.application.interfaces import SubscriptionSnapshot
from app.application.telegram_command_rate_limit import NoopAllowAllTelegramCommandRateLimiter
from app.bot_transport.runtime_facade import handle_slice1_telegram_update_to_rendered_message
from app.issuance.fake_provider import FakeIssuanceProvider, FakeProviderMode
from app.issuance.service import IssuanceService
from app.persistence.postgres_issuance_state import IssuanceStatePersistence, PostgresIssuanceStateRepository
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository
from app.persistence.slice1_postgres_wiring import resolve_slice1_composition_for_runtime
from app.runtime.payment_fulfillment_ingress import (
    DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS,
    PAYMENT_SIGNATURE_HEADER,
    PAYMENT_TIMESTAMP_HEADER,
    FulfillmentIngressSettings,
    create_payment_fulfillment_ingress_app,
)
from app.security.config import load_runtime_config
from app.shared.types import SubscriptionSnapshotState

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"
_TRUTHY = {"1", "true", "yes"}
_STDOUT_OK = "customer_journey_e2e: ok"
_STDERR_FAIL = "customer_journey_e2e: fail"
_STDERR_FAILED = "customer_journey_e2e: failed"
_SLICE1_POSTGRES_REPOS_ENV = "SLICE1_USE_POSTGRES_REPOS"
_REQUIRED_ENV = (
    "SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS",
    "TELEGRAM_ACCESS_RESEND_ENABLE",
)
_FORBIDDEN = (
    "DATABASE_URL",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "PRIVATE KEY",
    "BEGIN ",
    "token=",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "schema_version",
    "x-payment-signature",
    "x-payment-timestamp",
    "payment_fulfillment_webhook_secret",
)
_ADM02_PRINCIPAL = "adm02-customer-journey-e2e-smoke"


class _SyntheticIds(NamedTuple):
    telegram_user_id: int
    internal_user_id: str
    correlation_id: str
    billing_external_event_id: str
    external_payment_id: str


class _CheckoutReference(NamedTuple):
    reference_id: str
    reference_proof: str


def _assert_no_forbidden_output(text: str) -> None:
    upper = text.upper()
    for needle in _FORBIDDEN:
        if needle.upper() in upper:
            raise RuntimeError("customer journey smoke output leak guard failed")


def _print_stdout_safe(line: str) -> None:
    _assert_no_forbidden_output(line)
    print(line, flush=True)


def _print_stderr_safe(line: str) -> None:
    _assert_no_forbidden_output(line)
    print(line, file=sys.stderr, flush=True)


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY


def _require_env_opt_ins() -> None:
    for name in _REQUIRED_ENV:
        if not _truthy(os.environ.get(name)):
            raise RuntimeError("required smoke opt-ins are not enabled")
    checkout_secret = os.environ.get("TELEGRAM_CHECKOUT_REFERENCE_SECRET", "").strip()
    if not checkout_secret:
        raise RuntimeError("required smoke opt-ins are not enabled")


def _required_database_url() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("missing database dsn")
    return dsn


def _fulfillment_secret() -> str:
    secret = os.environ.get("PAYMENT_FULFILLMENT_WEBHOOK_SECRET", "").strip()
    if not secret:
        # Test-only fallback; this script never prints this value.
        return "customer_journey_test_secret_32_bytes"
    return secret


def _new_ids() -> _SyntheticIds:
    suffix = uuid.uuid4().hex[:10]
    numeric_suffix = int(suffix, 16) % 1_000_000_000
    telegram_user_id = 710_000_000 + numeric_suffix
    return _SyntheticIds(
        telegram_user_id=telegram_user_id,
        internal_user_id=f"u{telegram_user_id}",
        correlation_id=uuid.uuid4().hex,
        billing_external_event_id=f"journey-{suffix}-evt",
        external_payment_id=f"journey-{suffix}-pay",
    )


def _build_private_update(*, text: str, user_id: int, update_id: int) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": user_id, "is_bot": False, "first_name": "U"},
            "chat": {"id": user_id, "type": "private"},
            "text": text,
        },
    }


async def _render_command(*, command: str, ids: _SyntheticIds, composition, update_id: int):
    update = _build_private_update(text=command, user_id=ids.telegram_user_id, update_id=update_id)
    rendered = await handle_slice1_telegram_update_to_rendered_message(
        update,
        composition,
        correlation_id=ids.correlation_id,
    )
    _assert_no_forbidden_output(
        f"{command}|{rendered.correlation_id}|{rendered.message_text}|{rendered.action_keys}|{rendered.reply_markup}"
    )
    return rendered


def _assert_contains(text: str, fragment: str) -> None:
    if fragment not in text:
        raise RuntimeError("expected customer copy fragment is missing")


def _sign_payload(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


async def _send_fulfillment(
    *,
    app,
    secret: str,
    body_payload: dict[str, object],
    timestamp: int,
    signature_mode: str,
) -> int:
    raw = json.dumps(body_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = _sign_payload(secret, str(timestamp), raw)
    if signature_mode == "invalid":
        sig = "sha256=" + ("0" * 64)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/billing/fulfillment/webhook",
            content=raw,
            headers={
                PAYMENT_TIMESTAMP_HEADER: str(timestamp),
                PAYMENT_SIGNATURE_HEADER: sig,
                "content-type": "application/json",
            },
        )
    _assert_no_forbidden_output(response.text)
    return response.status_code


def _extract_checkout_reference(message_text: str) -> _CheckoutReference:
    marker = "Оформить подписку: "
    if marker not in message_text:
        raise RuntimeError("buy copy is missing checkout marker")
    checkout_url = message_text.split(marker, 1)[1].strip()
    query = parse_qs(urlsplit(checkout_url).query, keep_blank_values=True)
    ref_ids = query.get("client_reference_id", [])
    ref_proofs = query.get("client_reference_proof", [])
    if len(ref_ids) != 1 or len(ref_proofs) != 1:
        raise RuntimeError("checkout reference query fields are missing")
    return _CheckoutReference(reference_id=ref_ids[0], reference_proof=ref_proofs[0])


def _build_fulfillment_payload(ids: _SyntheticIds, reference: _CheckoutReference) -> dict[str, object]:
    return {
        "schema_version": 1,
        "external_event_id": ids.billing_external_event_id,
        "external_payment_id": ids.external_payment_id,
        "telegram_user_id": ids.telegram_user_id,
        "client_reference_id": reference.reference_id,
        "client_reference_proof": reference.reference_proof,
        "period_days": 30,
        "paid_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }


async def _cleanup(conn: asyncpg.Connection, ids: _SyntheticIds) -> None:
    await conn.execute(
        "DELETE FROM billing_subscription_apply_audit_events WHERE external_event_id = $1::text",
        ids.billing_external_event_id,
    )
    await conn.execute(
        """
        DELETE FROM billing_subscription_apply_records
        WHERE internal_fact_ref IN (
            SELECT internal_fact_ref
            FROM billing_events_ledger
            WHERE external_event_id = $1::text
        )
        """,
        ids.billing_external_event_id,
    )
    await conn.execute(
        "DELETE FROM billing_ingestion_audit_events WHERE external_event_id = $1::text",
        ids.billing_external_event_id,
    )
    await conn.execute(
        "DELETE FROM billing_events_ledger WHERE external_event_id = $1::text",
        ids.billing_external_event_id,
    )
    await conn.execute(
        "DELETE FROM issuance_state WHERE internal_user_id = $1::text",
        ids.internal_user_id,
    )
    await conn.execute(
        "DELETE FROM subscription_snapshots WHERE internal_user_id = $1::text",
        ids.internal_user_id,
    )
    await conn.execute(
        "DELETE FROM user_identities WHERE telegram_user_id = $1::bigint",
        ids.telegram_user_id,
    )


async def _resolve_postgres_composition(pool: asyncpg.Pool):
    old = os.environ.get(_SLICE1_POSTGRES_REPOS_ENV)
    os.environ[_SLICE1_POSTGRES_REPOS_ENV] = "1"
    try:
        config = load_runtime_config()

        async def _reuse_pool(_dsn: str) -> asyncpg.Pool:
            return pool

        composition, _ = await resolve_slice1_composition_for_runtime(config, open_pool=_reuse_pool)
        # Smoke test exercises the full journey (pending → active → expired) making more
        # ACCESS_RESEND calls than the default dispatcher rate limit allows in one window.
        # Rate limiting is tested separately; disable it here to keep the journey assertions clean.
        from dataclasses import replace as _dc_replace

        composition = _dc_replace(composition, command_rate_limiter=NoopAllowAllTelegramCommandRateLimiter())
        return composition
    finally:
        if old is None:
            os.environ.pop(_SLICE1_POSTGRES_REPOS_ENV, None)
        else:
            os.environ[_SLICE1_POSTGRES_REPOS_ENV] = old


def _build_adm02_handler(pool: asyncpg.Pool):
    identities = PostgresUserIdentityRepository(pool)
    snapshots = PostgresSubscriptionSnapshotReader(pool)
    issuance_state = PostgresIssuanceStateRepository(pool)
    return build_adm02_ensure_access_handler(
        identity=Adm01IdentityResolveAdapter(identities),
        subscription=Adm01PostgresSubscriptionReadAdapter(snapshots),
        issuance=Adm01PostgresIssuanceReadAdapter(issuance_state),
        mutation=Adm02EnsureAccessIssuanceMutationAdapter(
            IssuanceService(
                FakeIssuanceProvider(FakeProviderMode.SUCCESS),
                operational_state=issuance_state,
            )
        ),
        audit=None,
        adm02_allowlisted_internal_admin_principal_ids=[_ADM02_PRINCIPAL],
        adm02_mutation_opt_in_enabled=True,
    )


async def _ensure_active_snapshot(pool: asyncpg.Pool, ids: _SyntheticIds) -> None:
    snapshots = PostgresSubscriptionSnapshotReader(pool)
    snap = await snapshots.get_for_user(ids.internal_user_id)
    if snap is None:
        raise RuntimeError("snapshot missing")
    if snap.state_label != SubscriptionSnapshotState.ACTIVE.value:
        raise RuntimeError("snapshot not active")


async def _reconcile_expired_access(pool: asyncpg.Pool) -> int:
    repo = PostgresIssuanceStateRepository(pool)
    return await repo.reconcile_expired_active_subscriptions(now_utc=datetime.now(UTC))


async def run_customer_journey_e2e() -> None:
    _require_env_opt_ins()
    dsn = _required_database_url()
    ids = _new_ids()
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        async with pool.acquire() as conn:
            await _cleanup(conn, ids)

        composition = await _resolve_postgres_composition(pool)

        # The composition uses StubVlessProvider (no VPN servers in CI DB).
        # Seed the provider's in-memory user set so get_user_config succeeds
        # during the /get_access RESEND flow's get_safe_delivery_instructions call.
        if composition.vless_provider is not None:
            await composition.vless_provider.create_user(internal_user_id=ids.internal_user_id)

        # Before fulfillment (pending/inactive customer-facing state)
        start = await _render_command(command="/start", ids=ids, composition=composition, update_id=1)
        _assert_contains(start.message_text, "Добро пожаловать")
        if not isinstance(start.reply_markup, dict) or (
            "keyboard" not in start.reply_markup and "inline_keyboard" not in start.reply_markup
        ):
            raise RuntimeError("start must provide reply keyboard")

        plans = await _render_command(command="/plans", ids=ids, composition=composition, update_id=2)
        _assert_contains(plans.message_text, "Используйте /buy")

        buy = await _render_command(command="/buy", ids=ids, composition=composition, update_id=3)
        if "Оформить подписку:" not in buy.message_text and "Оплата пока не настроена" not in buy.message_text:
            raise RuntimeError("buy copy mismatch")

        checkout = await _render_command(command="/checkout", ids=ids, composition=composition, update_id=4)
        if checkout.message_text != buy.message_text:
            raise RuntimeError("checkout alias must mirror buy")
        checkout_reference = _extract_checkout_reference(buy.message_text)

        pending_success = await _render_command(command="/success", ids=ids, composition=composition, update_id=5)
        _assert_contains(pending_success.message_text, "Активация может занять")

        pending_status = await _render_command(
            command="/my_subscription", ids=ids, composition=composition, update_id=6
        )
        if "активн" in pending_status.message_text.lower():
            raise RuntimeError("pending status must not claim active")

        pending_access = await _render_command(command="/get_access", ids=ids, composition=composition, update_id=7)
        if "принят" in pending_access.message_text.lower():
            raise RuntimeError("pending access request must not be accepted")

        # Signed fulfillment ingress (invalid -> valid -> duplicate valid)
        secret = _fulfillment_secret()
        ingress_settings = FulfillmentIngressSettings(
            secret=secret,
            provider_key="provider_agnostic_v1",
            max_age_seconds=300,
            checkout_reference_secret=os.environ.get("TELEGRAM_CHECKOUT_REFERENCE_SECRET", "").strip() or None,
            checkout_reference_max_age_seconds=DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS,
            strict_checkout_reference_required=True,
        )
        ingress_app = create_payment_fulfillment_ingress_app(pool=pool, settings=ingress_settings)
        payload = _build_fulfillment_payload(ids, checkout_reference)
        now_epoch = int(time.time())

        invalid_sig_status = await _send_fulfillment(
            app=ingress_app,
            secret=secret,
            body_payload=payload,
            timestamp=now_epoch,
            signature_mode="invalid",
        )
        if invalid_sig_status != 401:
            raise RuntimeError("invalid signature must be rejected")
        still_pending = await _render_command(command="/success", ids=ids, composition=composition, update_id=8)
        _assert_contains(still_pending.message_text, "Активация может занять")

        stale_payload = dict(payload)
        stale_payload["external_event_id"] = f"{ids.billing_external_event_id}-stale"
        stale_payload["external_payment_id"] = f"{ids.external_payment_id}-stale"
        stale_payload["paid_at"] = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat()
        stale_status = await _send_fulfillment(
            app=ingress_app,
            secret=secret,
            body_payload=stale_payload,
            timestamp=now_epoch,
            signature_mode="valid",
        )
        if stale_status != 400:
            raise RuntimeError("stale checkout reference must be rejected")
        stale_pending = await _render_command(command="/success", ids=ids, composition=composition, update_id=81)
        _assert_contains(stale_pending.message_text, "Активация может занять")

        tampered_payload = dict(payload)
        tampered_payload["external_event_id"] = f"{ids.billing_external_event_id}-tampered"
        tampered_payload["external_payment_id"] = f"{ids.external_payment_id}-tampered"
        tampered_payload["client_reference_proof"] = "0" * 64
        tampered_status = await _send_fulfillment(
            app=ingress_app,
            secret=secret,
            body_payload=tampered_payload,
            timestamp=now_epoch,
            signature_mode="valid",
        )
        if tampered_status != 400:
            raise RuntimeError("tampered checkout reference must be rejected")
        tampered_pending = await _render_command(command="/success", ids=ids, composition=composition, update_id=82)
        _assert_contains(tampered_pending.message_text, "Активация может занять")

        valid_status = await _send_fulfillment(
            app=ingress_app,
            secret=secret,
            body_payload=payload,
            timestamp=now_epoch,
            signature_mode="valid",
        )
        if valid_status != 200:
            raise RuntimeError("valid fulfillment must be accepted")
        duplicate_status = await _send_fulfillment(
            app=ingress_app,
            secret=secret,
            body_payload=payload,
            timestamp=now_epoch,
            signature_mode="valid",
        )
        if duplicate_status != 200:
            raise RuntimeError("duplicate fulfillment must be idempotent success")

        await _ensure_active_snapshot(pool, ids)

        active_success = await _render_command(command="/success", ids=ids, composition=composition, update_id=9)
        _assert_contains(active_success.message_text, "Подписка активна")
        active_status = await _render_command(
            command="/my_subscription", ids=ids, composition=composition, update_id=10
        )
        _assert_contains(active_status.message_text.lower(), "активн")

        # Existing safe operator fixture path: ensure access via ADM-02 internal handler.
        adm02 = _build_adm02_handler(pool)
        ensure_access = await execute_adm02_ensure_access_endpoint(
            handler=adm02,
            principal_extractor=DefaultInternalAdminPrincipalExtractor(),
            request=Adm02EnsureAccessInboundRequest(
                correlation_id=ids.correlation_id,
                internal_admin_principal_id=_ADM02_PRINCIPAL,
                telegram_user_id=ids.telegram_user_id,
            ),
        )
        if ensure_access.outcome != "success":
            raise RuntimeError("adm02 ensure-access failed")

        get_access = await _render_command(command="/get_access", ids=ids, composition=composition, update_id=11)
        _assert_contains(get_access.message_text.lower(), "принят")
        resend_access = await _render_command(command="/resend_access", ids=ids, composition=composition, update_id=12)
        if "подождите" not in resend_access.message_text.lower() and "принят" not in resend_access.message_text.lower():
            raise RuntimeError("resend access must stay idempotent-safe")

        renew = await _render_command(command="/renew", ids=ids, composition=composition, update_id=121)
        _assert_contains(renew.message_text, "Продлить подписку:")
        _assert_contains(renew.message_text, "client_reference_id=")
        _assert_contains(renew.message_text, "client_reference_proof=")

        # Expired path is validated via persisted active_until boundary.
        await PostgresSubscriptionSnapshotReader(pool).upsert_state(
            SubscriptionSnapshot(
                internal_user_id=ids.internal_user_id,
                state_label=SubscriptionSnapshotState.ACTIVE.value,
                active_until_utc=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        expired_status = await _render_command(
            command="/my_subscription", ids=ids, composition=composition, update_id=122
        )
        _assert_contains(expired_status.message_text.lower(), "истекл")
        _assert_contains(expired_status.message_text, "/renew")

        # Reconcile before expired-path resend commands: the resend handler contains a best-effort
        # proactive revoke path that fires when the subscription is expired. Running reconcile first
        # ensures the reconcile (not the handler) performs the authoritative state transition.
        reconciled_rows = await _reconcile_expired_access(pool)
        if reconciled_rows < 1:
            raise RuntimeError("expired access reconcile must revoke at least one issued row")
        reconciled_rows_second = await _reconcile_expired_access(pool)
        if reconciled_rows_second != 0:
            raise RuntimeError("expired access reconcile must be idempotent on repeat run")
        current_after_reconcile = await PostgresIssuanceStateRepository(pool).get_current_for_user(ids.internal_user_id)
        if current_after_reconcile is None or current_after_reconcile.state is not IssuanceStatePersistence.REVOKED:
            raise RuntimeError("expired access reconcile did not mark issuance as revoked")

        expired_access = await _render_command(command="/get_access", ids=ids, composition=composition, update_id=123)
        _assert_contains(expired_access.message_text, "/renew")
        expired_resend = await _render_command(
            command="/resend_access", ids=ids, composition=composition, update_id=124
        )
        _assert_contains(expired_resend.message_text, "/renew")

        support = await _render_command(command="/support", ids=ids, composition=composition, update_id=13)
        _assert_contains(support.message_text.lower(), "поддержк")
    finally:
        try:
            async with pool.acquire() as conn:
                await _cleanup(conn, ids)
        finally:
            await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        asyncio.run(run_customer_journey_e2e())
    except RuntimeError as exc:
        _print_stderr_safe(_STDERR_FAIL)
        # Emit a bounded reason marker to make CI triage possible without
        # printing sensitive values or full tracebacks.
        msg = (str(exc) or "").strip().lower()
        if msg == "required smoke opt-ins are not enabled":
            code = "required_opt_in_missing"
        elif msg == "expected customer copy fragment is missing":
            code = "customer_copy_missing"
        elif msg == "resend access must stay idempotent-safe":
            code = "resend_access_not_idempotent_safe"
        elif msg == "adm02 ensure-access failed":
            code = "adm02_ensure_access_failed"
        elif msg == "expired access reconcile must revoke at least one issued row":
            code = "reconcile_no_rows_revoked"
        elif msg == "expired access reconcile must be idempotent on repeat run":
            code = "reconcile_not_idempotent"
        elif msg == "expired access reconcile did not mark issuance as revoked":
            code = "reconcile_state_not_revoked"
        else:
            code = "runtime_error"
        _print_stderr_safe(f"issue_code={code}")
        return 1
    except Exception:
        _print_stderr_safe(_STDERR_FAILED)
        return 1
    _print_stdout_safe(_STDOUT_OK)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
