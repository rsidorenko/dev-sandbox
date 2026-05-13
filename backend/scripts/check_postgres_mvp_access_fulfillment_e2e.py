"""PostgreSQL MVP e2e smoke: synthetic active user -> ADM-02 ensure-access -> /get_access resend accepted."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import asyncpg

from app.admin_support.adm01_endpoint import Adm01InboundRequest, execute_adm01_endpoint
from app.admin_support.adm01_identity_resolve_adapter import Adm01IdentityResolveAdapter
from app.admin_support.adm01_postgres_issuance_read_adapter import Adm01PostgresIssuanceReadAdapter
from app.admin_support.adm01_postgres_subscription_read_adapter import Adm01PostgresSubscriptionReadAdapter
from app.admin_support.adm01_subscription_entitlement_read_adapter import (
    Adm01SubscriptionEntitlementReadAdapter,
)
from app.admin_support.adm01_subscription_policy_read_adapter import Adm01SubscriptionPolicyReadAdapter
from app.admin_support.adm01_wiring import build_adm01_lookup_handler
from app.admin_support.adm02_ensure_access_audit_postgres import PostgresAdm02EnsureAccessAuditSink
from app.admin_support.adm02_ensure_access_audit_read_endpoint import (
    Adm02EnsureAccessAuditLookupInboundRequest,
    execute_adm02_ensure_access_audit_lookup_endpoint,
)
from app.admin_support.adm02_ensure_access_endpoint import (
    Adm02EnsureAccessInboundRequest,
    execute_adm02_ensure_access_endpoint,
)
from app.admin_support.adm02_ensure_access_mutation import Adm02EnsureAccessIssuanceMutationAdapter
from app.admin_support.adm02_postgres_ensure_access_audit_read_adapter import (
    Adm02PostgresEnsureAccessAuditReadAdapter,
)
from app.admin_support.adm02_wiring import (
    build_adm02_ensure_access_audit_lookup_handler,
    build_adm02_ensure_access_handler,
)
from app.admin_support.contracts import (
    Adm01SupportAccessReadinessBucket,
    Adm01SupportNextAction,
    Adm01SupportSubscriptionBucket,
    Adm02EnsureAccessAuditEvent,
    Adm02EnsureAccessAuditEventType,
    Adm02EnsureAccessAuditOutcomeBucket,
    Adm02EnsureAccessAuditPort,
    Adm02EnsureAccessAuditPrincipalMarker,
    Adm02EnsureAccessRemediationResult,
)
from app.admin_support.principal_extraction import DefaultInternalAdminPrincipalExtractor
from app.application.billing_ingestion_main import (
    async_run_billing_ingest_from_parsed,
    parse_json_to_normalized_billing_input,
)
from app.application.billing_subscription_apply_main import async_run_apply
from app.bot_transport.dispatcher import dispatch_slice1_transport
from app.bot_transport.normalized import TransportIncomingEnvelope
from app.bot_transport.presentation import (
    TransportAccessResendCode,
    TransportResponseCategory,
    TransportStatusCode,
)
from app.domain.billing_apply_rules import UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED
from app.issuance.fake_provider import FakeIssuanceProvider, FakeProviderMode
from app.issuance.service import IssuanceService
from app.persistence.postgres_issuance_state import IssuanceStatePersistence, PostgresIssuanceStateRepository
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository
from app.persistence.slice1_postgres_wiring import resolve_slice1_composition_for_runtime
from app.security.config import load_runtime_config
from app.shared.types import OperationOutcomeCategory, SubscriptionSnapshotState

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"
_TRUTHY_ENV_VALUES = {"1", "true", "yes"}
_REQUIRED_DSN_ENV = "DATABASE_URL"
_REQUIRED_OPT_INS = (
    "SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS",
    "BILLING_NORMALIZED_INGEST_ENABLE",
    "BILLING_SUBSCRIPTION_APPLY_ENABLE",
    "ISSUANCE_OPERATOR_ENABLE",
    "TELEGRAM_ACCESS_RESEND_ENABLE",
    "ADM02_ENSURE_ACCESS_ENABLE",
)
_SLICE1_POSTGRES_REPOS_ENV = "SLICE1_USE_POSTGRES_REPOS"
_FORBIDDEN_OUTPUT_FRAGMENTS = (
    "DATABASE_URL",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "PRIVATE KEY",
    "BEGIN ",
    "token=",
    "vpn://",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "schema_version",
    "billing_provider_key",
    "external_event_id",
    "internal_fact_ref",
    "checkout_attempt_id",
    "customer_ref",
    "provider_ref",
    "internal_user_id",
)
_STDOUT_OK = "postgres_mvp_access_fulfillment_e2e: ok"
_STDERR_FAIL = "postgres_mvp_access_fulfillment_e2e: fail"
_STDERR_FAILED = "postgres_mvp_access_fulfillment_e2e: failed"
_SYNTHETIC_PREFIX = "mvp-access-e2e-"
_ADM01_SMOKE_PRINCIPAL = "adm01-mvp-access-fulfillment-e2e-smoke"
_ADM02_SMOKE_PRINCIPAL = "adm02-mvp-access-fulfillment-e2e-smoke"
_ADM02_AUDIT_SOURCE_MARKER = "adm02_mvp_access_fulfillment_e2e_smoke"


class _CollectingAdm02EnsureAccessAuditSink(Adm02EnsureAccessAuditPort):
    def __init__(self) -> None:
        self.events: list[Adm02EnsureAccessAuditEvent] = []

    async def append_ensure_access_event(self, event: Adm02EnsureAccessAuditEvent) -> None:
        self.events.append(event)


class _SyntheticIds(NamedTuple):
    telegram_user_id: int
    internal_user_id: str
    internal_fact_ref: str
    billing_external_event_id: str
    correlation_id: str
    telegram_update_id: int

    @property
    def resend_idempotency_key(self) -> str:
        return f"tg-resend:{self.telegram_user_id}:{self.telegram_update_id}"


def _assert_no_forbidden_output(text: str) -> None:
    upper_text = text.upper()
    for frag in _FORBIDDEN_OUTPUT_FRAGMENTS:
        if frag.upper() in upper_text:
            raise RuntimeError("access fulfillment smoke output leak guard failed")


def _print_stdout_safe(line: str) -> None:
    _assert_no_forbidden_output(line)
    print(line, flush=True)


def _print_stderr_safe(line: str) -> None:
    _assert_no_forbidden_output(line)
    print(line, file=sys.stderr, flush=True)


def _is_truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY_ENV_VALUES


def _require_opt_ins() -> None:
    for env_name in _REQUIRED_OPT_INS:
        if not _is_truthy_env(os.environ.get(env_name)):
            raise RuntimeError("required opt-ins are not enabled")


def _required_database_url() -> str:
    dsn = os.environ.get(_REQUIRED_DSN_ENV, "").strip()
    if not dsn:
        raise RuntimeError("missing database dsn")
    return dsn


def _new_synthetic_ids() -> _SyntheticIds:
    suffix = uuid.uuid4().hex[:10]
    numeric_suffix = int(suffix, 16) % 1_000_000_000
    telegram_user_id = 700_000_000 + numeric_suffix
    internal_user_id = f"u{telegram_user_id}"
    internal_fact_ref = f"{_SYNTHETIC_PREFIX}{suffix}-fact"
    billing_external_event_id = f"{_SYNTHETIC_PREFIX}{suffix}-event"
    correlation_id = uuid.uuid4().hex
    telegram_update_id = 800_000_000 + (numeric_suffix % 100_000_000)
    return _SyntheticIds(
        telegram_user_id=telegram_user_id,
        internal_user_id=internal_user_id,
        internal_fact_ref=internal_fact_ref,
        billing_external_event_id=billing_external_event_id,
        correlation_id=correlation_id,
        telegram_update_id=telegram_update_id,
    )


async def _cleanup_synthetic_rows(conn: asyncpg.Connection, ids: _SyntheticIds) -> None:
    await conn.execute(
        """
        DELETE FROM adm02_ensure_access_audit_events
        WHERE correlation_id = $1::text
          AND source_marker = $2::text
        """,
        ids.correlation_id,
        _ADM02_AUDIT_SOURCE_MARKER,
    )
    await conn.execute(
        "DELETE FROM issuance_state WHERE internal_user_id = $1::text",
        ids.internal_user_id,
    )
    await conn.execute(
        "DELETE FROM billing_subscription_apply_audit_events WHERE internal_fact_ref = $1::text",
        ids.internal_fact_ref,
    )
    await conn.execute(
        "DELETE FROM billing_subscription_apply_records WHERE internal_fact_ref = $1::text",
        ids.internal_fact_ref,
    )
    await conn.execute(
        "DELETE FROM billing_ingestion_audit_events WHERE external_event_id = $1::text",
        ids.billing_external_event_id,
    )
    await conn.execute(
        "DELETE FROM billing_events_ledger WHERE internal_fact_ref = $1::text",
        ids.internal_fact_ref,
    )
    await conn.execute(
        "DELETE FROM idempotency_records WHERE idempotency_key = ANY($1::text[])",
        [ids.resend_idempotency_key],
    )
    await conn.execute(
        "DELETE FROM subscription_snapshots WHERE internal_user_id = $1::text",
        ids.internal_user_id,
    )
    await conn.execute(
        "DELETE FROM user_identities WHERE telegram_user_id = $1::bigint",
        ids.telegram_user_id,
    )


def _normalized_fact_json(ids: _SyntheticIds) -> str:
    now_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    return (
        "{"
        f'"schema_version":1,'
        f'"billing_provider_key":"operator_access_fulfillment_e2e_provider",'
        f'"external_event_id":"{ids.billing_external_event_id}",'
        f'"event_type":"{UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED}",'
        f'"event_effective_at":"{now_utc}",'
        f'"event_received_at":"{now_utc}",'
        '"status":"accepted",'
        f'"ingestion_correlation_id":"{ids.correlation_id}",'
        f'"internal_fact_ref":"{ids.internal_fact_ref}",'
        f'"internal_user_id":"{ids.internal_user_id}"'
        "}"
    )


async def _run_billing_ingest_apply_for_active_subscription(ids: _SyntheticIds, dsn: str) -> None:
    parsed = parse_json_to_normalized_billing_input(_normalized_fact_json(ids))
    ingest_outcome_1, ingest_ref_1, _, _ = await async_run_billing_ingest_from_parsed(parsed, dsn=dsn)
    if ingest_ref_1 != ids.internal_fact_ref:
        raise RuntimeError("ingest returned mismatched internal_fact_ref")
    if ingest_outcome_1 != "accepted":
        raise RuntimeError("first ingest must accept fresh synthetic fact")

    ingest_outcome_2, ingest_ref_2, _, _ = await async_run_billing_ingest_from_parsed(parsed, dsn=dsn)
    if ingest_ref_2 != ids.internal_fact_ref:
        raise RuntimeError("second ingest returned mismatched internal_fact_ref")
    if ingest_outcome_2 != "idempotent_replay":
        raise RuntimeError("second ingest must be idempotent_replay")

    apply_result = await async_run_apply(ids.internal_fact_ref, dsn=dsn)
    if apply_result.operation_outcome not in (
        OperationOutcomeCategory.SUCCESS,
        OperationOutcomeCategory.IDEMPOTENT_NOOP,
    ):
        raise RuntimeError("apply did not produce success or idempotent_noop")

    apply_result_2 = await async_run_apply(ids.internal_fact_ref, dsn=dsn)
    if apply_result_2.operation_outcome is not OperationOutcomeCategory.IDEMPOTENT_NOOP:
        raise RuntimeError("second apply must be idempotent_noop")


async def _assert_active_subscription(pool: asyncpg.Pool, ids: _SyntheticIds) -> None:
    snapshot_reader = PostgresSubscriptionSnapshotReader(pool)
    snapshot = await snapshot_reader.get_for_user(ids.internal_user_id)
    if snapshot is None:
        raise RuntimeError("subscription snapshot not found")
    if snapshot.state_label != SubscriptionSnapshotState.ACTIVE.value:
        raise RuntimeError("subscription snapshot is not active")


async def _resolve_postgres_composition_with_existing_pool(pool: asyncpg.Pool):
    old_value = os.environ.get(_SLICE1_POSTGRES_REPOS_ENV)
    os.environ[_SLICE1_POSTGRES_REPOS_ENV] = "1"
    try:
        config = load_runtime_config()

        async def _reuse_pool(_dsn: str) -> asyncpg.Pool:
            return pool

        composition, _ = await resolve_slice1_composition_for_runtime(config, open_pool=_reuse_pool)
        return composition
    finally:
        if old_value is None:
            os.environ.pop(_SLICE1_POSTGRES_REPOS_ENV, None)
        else:
            os.environ[_SLICE1_POSTGRES_REPOS_ENV] = old_value


async def _dispatch_transport_command(
    *,
    command: str,
    ids: _SyntheticIds,
    composition,
):
    return await dispatch_slice1_transport(
        TransportIncomingEnvelope(
            telegram_user_id=ids.telegram_user_id,
            correlation_id=ids.correlation_id,
            telegram_update_id=ids.telegram_update_id,
            normalized_command_text=command,
        ),
        composition,
    )


def _assert_transport_success_code(response, *, expected_code: str) -> None:
    response_blob = (
        f"{response.category.value}|{response.code}|{response.correlation_id}|{response.next_action_hint or ''}"
    )
    _assert_no_forbidden_output(response_blob)
    if response.category is not TransportResponseCategory.SUCCESS:
        raise RuntimeError("transport response is not success")
    if response.code != expected_code:
        raise RuntimeError("transport response code mismatch")


def _build_adm01_lookup_handler_with_existing_pool(pool: asyncpg.Pool):
    identities = PostgresUserIdentityRepository(pool)
    snapshots = PostgresSubscriptionSnapshotReader(pool)
    issuance_state = PostgresIssuanceStateRepository(pool)
    return build_adm01_lookup_handler(
        identity=Adm01IdentityResolveAdapter(identities),
        subscription=Adm01PostgresSubscriptionReadAdapter(snapshots),
        entitlement=Adm01SubscriptionEntitlementReadAdapter(snapshots),
        issuance=Adm01PostgresIssuanceReadAdapter(issuance_state),
        policy=Adm01SubscriptionPolicyReadAdapter(snapshots),
        redaction=None,
        adm01_allowlisted_internal_admin_principal_ids=[_ADM01_SMOKE_PRINCIPAL],
    )


def _build_adm02_ensure_access_handler_with_existing_pool(
    pool: asyncpg.Pool,
    *,
    audit_sink: Adm02EnsureAccessAuditPort | None = None,
):
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
        audit=audit_sink,
        adm02_allowlisted_internal_admin_principal_ids=[_ADM02_SMOKE_PRINCIPAL],
        adm02_mutation_opt_in_enabled=True,
    )


def _build_adm02_ensure_access_audit_lookup_handler_with_existing_pool(pool: asyncpg.Pool):
    return build_adm02_ensure_access_audit_lookup_handler(
        audit_read=Adm02PostgresEnsureAccessAuditReadAdapter(pool),
        adm02_allowlisted_internal_admin_principal_ids=[_ADM02_SMOKE_PRINCIPAL],
    )


async def _assert_adm02_safe_successful_remediation(
    *,
    handler,
    ids: _SyntheticIds,
    expected_remediation_results: tuple[Adm02EnsureAccessRemediationResult, ...],
) -> None:
    response = await execute_adm02_ensure_access_endpoint(
        handler=handler,
        principal_extractor=DefaultInternalAdminPrincipalExtractor(),
        request=Adm02EnsureAccessInboundRequest(
            correlation_id=ids.correlation_id,
            internal_admin_principal_id=_ADM02_SMOKE_PRINCIPAL,
            telegram_user_id=ids.telegram_user_id,
        ),
    )
    if response.outcome != "success":
        raise RuntimeError("adm02 ensure-access outcome mismatch")
    if response.summary is None:
        raise RuntimeError("adm02 ensure-access summary missing")
    summary = response.summary
    if summary.telegram_identity_known is not True:
        raise RuntimeError("adm02 ensure-access identity marker mismatch")
    if summary.subscription_bucket != Adm01SupportSubscriptionBucket.ACTIVE.value:
        raise RuntimeError("adm02 ensure-access subscription bucket mismatch")
    if summary.access_readiness_bucket != Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY.value:
        raise RuntimeError("adm02 ensure-access readiness bucket mismatch")
    if summary.remediation_result not in {result.value for result in expected_remediation_results}:
        raise RuntimeError("adm02 ensure-access remediation result mismatch")
    if summary.recommended_next_action != Adm01SupportNextAction.ASK_USER_TO_USE_GET_ACCESS.value:
        raise RuntimeError("adm02 ensure-access next action mismatch")
    summary_blob = (
        "adm02|"
        f"{summary.telegram_identity_known}|"
        f"{summary.subscription_bucket}|"
        f"{summary.access_readiness_bucket}|"
        f"{summary.remediation_result}|"
        f"{summary.recommended_next_action}|"
        f"{response.correlation_id}|"
        f"{response.outcome}"
    )
    _assert_no_forbidden_output(summary_blob)


async def _assert_adm02_idempotent_repeat_with_stable_current_issued_state(
    *,
    handler,
    pool: asyncpg.Pool,
    ids: _SyntheticIds,
) -> None:
    repo = PostgresIssuanceStateRepository(pool)
    current_before = await repo.get_current_for_user(ids.internal_user_id)
    if current_before is None or current_before.state is not IssuanceStatePersistence.ISSUED:
        raise RuntimeError("adm02 idempotency precondition mismatch")
    await _assert_adm02_safe_successful_remediation(
        handler=handler,
        ids=ids,
        expected_remediation_results=(Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY,),
    )
    current_after = await repo.get_current_for_user(ids.internal_user_id)
    if current_after is None or current_after.state is not IssuanceStatePersistence.ISSUED:
        raise RuntimeError("adm02 idempotency current issued state mismatch")
    if current_after.issue_idempotency_key != current_before.issue_idempotency_key:
        raise RuntimeError("adm02 idempotency current issue key mismatch")


def _assert_adm02_audit_evidence(events: tuple[Adm02EnsureAccessAuditEvent, ...]) -> None:
    if len(events) < 2:
        raise RuntimeError("adm02 ensure-access audit evidence missing")
    first = events[0]
    second = events[1]
    if first.outcome_bucket is not Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS:
        raise RuntimeError("adm02 ensure-access first audit outcome mismatch")
    if second.outcome_bucket is not Adm02EnsureAccessAuditOutcomeBucket.NOOP_ACCESS_ALREADY_READY:
        raise RuntimeError("adm02 ensure-access second audit outcome mismatch")
    for event in events:
        event_blob = (
            f"{event.event_type.value}|{event.outcome_bucket.value}|"
            f"{event.remediation_result.value if event.remediation_result is not None else ''}|"
            f"{event.readiness_bucket.value if event.readiness_bucket is not None else ''}|"
            f"{event.principal_marker.value}|{event.correlation_id}"
        )
        _assert_no_forbidden_output(event_blob)


async def _assert_adm02_durable_audit_readback(
    *,
    handler,
    ids: _SyntheticIds,
) -> None:
    response = await execute_adm02_ensure_access_audit_lookup_endpoint(
        handler=handler,
        principal_extractor=DefaultInternalAdminPrincipalExtractor(),
        request=Adm02EnsureAccessAuditLookupInboundRequest(
            correlation_id=ids.correlation_id,
            internal_admin_principal_id=_ADM02_SMOKE_PRINCIPAL,
            evidence_correlation_id=ids.correlation_id,
            limit=20,
        ),
    )
    if response.outcome != "success":
        raise RuntimeError("adm02 ensure-access audit readback outcome mismatch")
    if len(response.items) < 2:
        raise RuntimeError("adm02 ensure-access durable audit evidence missing")

    issued_seen = False
    already_ready_seen = False
    for item in response.items:
        if item.correlation_id != ids.correlation_id:
            raise RuntimeError("adm02 ensure-access audit readback correlation mismatch")
        if item.event_type != Adm02EnsureAccessAuditEventType.ENSURE_ACCESS.value:
            raise RuntimeError("adm02 ensure-access audit readback event type mismatch")
        if item.principal_marker != Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED.value:
            raise RuntimeError("adm02 ensure-access audit readback principal marker mismatch")
        if item.source_marker != _ADM02_AUDIT_SOURCE_MARKER:
            raise RuntimeError("adm02 ensure-access audit readback source marker mismatch")
        if item.outcome_bucket == Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS.value:
            issued_seen = True
        if item.outcome_bucket == Adm02EnsureAccessAuditOutcomeBucket.NOOP_ACCESS_ALREADY_READY.value:
            already_ready_seen = True
        item_blob = (
            f"{item.created_at}|{item.event_type}|{item.outcome_bucket}|"
            f"{item.remediation_result or ''}|{item.readiness_bucket or ''}|"
            f"{item.principal_marker}|{item.correlation_id}|{item.source_marker or ''}"
        )
        _assert_no_forbidden_output(item_blob)
    if not issued_seen or not already_ready_seen:
        raise RuntimeError("adm02 ensure-access durable audit outcome evidence mismatch")


async def _assert_adm01_support_readiness(
    *,
    handler,
    ids: _SyntheticIds,
    expected_access_readiness_bucket: Adm01SupportAccessReadinessBucket,
    expected_next_action: Adm01SupportNextAction,
) -> None:
    response = await execute_adm01_endpoint(
        handler=handler,
        principal_extractor=DefaultInternalAdminPrincipalExtractor(),
        request=Adm01InboundRequest(
            correlation_id=ids.correlation_id,
            internal_admin_principal_id=_ADM01_SMOKE_PRINCIPAL,
            telegram_user_id=ids.telegram_user_id,
        ),
    )
    if response.outcome != "success":
        raise RuntimeError("adm01 readiness lookup outcome mismatch")
    if response.summary is None:
        raise RuntimeError("adm01 readiness lookup summary missing")
    summary = response.summary
    if summary.telegram_identity_known is not True:
        raise RuntimeError("adm01 readiness identity marker mismatch")
    if summary.subscription_bucket != Adm01SupportSubscriptionBucket.ACTIVE.value:
        raise RuntimeError("adm01 readiness subscription bucket mismatch")
    if summary.access_readiness_bucket != expected_access_readiness_bucket.value:
        raise RuntimeError("adm01 readiness access bucket mismatch")
    if summary.recommended_next_action != expected_next_action.value:
        raise RuntimeError("adm01 readiness next action mismatch")
    summary_blob = (
        "adm01|"
        f"{summary.telegram_identity_known}|"
        f"{summary.subscription_bucket}|"
        f"{summary.access_readiness_bucket}|"
        f"{summary.recommended_next_action}|"
        f"{summary.redaction}|"
        f"{response.correlation_id}|"
        f"{response.outcome}"
    )
    _assert_no_forbidden_output(summary_blob)


async def run_postgres_mvp_access_fulfillment_e2e() -> None:
    _require_opt_ins()
    dsn = _required_database_url()
    ids = _new_synthetic_ids()
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        async with pool.acquire() as conn:
            await _cleanup_synthetic_rows(conn, ids)

        identity_repo = PostgresUserIdentityRepository(pool)
        identity = await identity_repo.create_if_absent(ids.telegram_user_id)
        if identity.internal_user_id != ids.internal_user_id:
            raise RuntimeError("identity mismatch")

        await _run_billing_ingest_apply_for_active_subscription(ids, dsn)
        await _assert_active_subscription(pool, ids)
        composition = await _resolve_postgres_composition_with_existing_pool(pool)
        adm01_lookup_handler = _build_adm01_lookup_handler_with_existing_pool(pool)
        adm02_audit_sink = PostgresAdm02EnsureAccessAuditSink(
            pool,
            source_marker=_ADM02_AUDIT_SOURCE_MARKER,
        )
        adm02_ensure_access_handler = _build_adm02_ensure_access_handler_with_existing_pool(
            pool,
            audit_sink=adm02_audit_sink,
        )
        adm02_audit_lookup_handler = _build_adm02_ensure_access_audit_lookup_handler_with_existing_pool(pool)

        status_before_issue = await _dispatch_transport_command(
            command="/status",
            ids=ids,
            composition=composition,
        )
        _assert_transport_success_code(
            status_before_issue,
            expected_code=TransportStatusCode.SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY.value,
        )
        await _assert_adm01_support_readiness(
            handler=adm01_lookup_handler,
            ids=ids,
            expected_access_readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_NOT_READY,
            expected_next_action=Adm01SupportNextAction.INVESTIGATE_ISSUANCE,
        )

        await _assert_adm02_safe_successful_remediation(
            handler=adm02_ensure_access_handler,
            ids=ids,
            expected_remediation_results=(Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,),
        )
        await _assert_adm02_idempotent_repeat_with_stable_current_issued_state(
            handler=adm02_ensure_access_handler,
            pool=pool,
            ids=ids,
        )
        await _assert_adm02_durable_audit_readback(
            handler=adm02_audit_lookup_handler,
            ids=ids,
        )

        status_after_issue = await _dispatch_transport_command(
            command="/status",
            ids=ids,
            composition=composition,
        )
        _assert_transport_success_code(
            status_after_issue,
            expected_code=TransportStatusCode.SUBSCRIPTION_ACTIVE_ACCESS_READY.value,
        )
        await _assert_adm01_support_readiness(
            handler=adm01_lookup_handler,
            ids=ids,
            expected_access_readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
            expected_next_action=Adm01SupportNextAction.ASK_USER_TO_USE_GET_ACCESS,
        )

        response = await _dispatch_transport_command(
            command="/get_access",
            ids=ids,
            composition=composition,
        )
        _assert_transport_success_code(
            response,
            expected_code=TransportAccessResendCode.RESEND_ACCEPTED.value,
        )
    finally:
        try:
            async with pool.acquire() as conn:
                await _cleanup_synthetic_rows(conn, ids)
        finally:
            await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        asyncio.run(run_postgres_mvp_access_fulfillment_e2e())
    except RuntimeError:
        _print_stderr_safe(_STDERR_FAIL)
        return 1
    except Exception:
        _print_stderr_safe(_STDERR_FAILED)
        return 1
    _print_stdout_safe(_STDOUT_OK)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
