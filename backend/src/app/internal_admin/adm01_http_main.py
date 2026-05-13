"""Standalone ADM-01 internal HTTP process entry (optional uvicorn listener; Telegram polling unchanged)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
import uvicorn
from starlette.applications import Starlette

from app.admin_support.adm01_wiring import (
    build_adm01_entitlement_read_from_postgres_snapshots,
    build_adm01_identity_resolve_from_postgres_user_identities,
    build_adm01_issuance_read_from_postgres_issuance_state,
    build_adm01_policy_read_from_postgres_snapshots,
    build_adm01_subscription_read_from_postgres_snapshots,
)
from app.admin_support.adm02_ensure_access_audit_logging import (
    FanoutAdm02EnsureAccessAuditSink,
    StructuredLoggingAdm02EnsureAccessAuditSink,
)
from app.admin_support.adm02_ensure_access_audit_postgres import PostgresAdm02EnsureAccessAuditSink
from app.admin_support.adm02_ensure_access_mutation import Adm02EnsureAccessIssuanceMutationAdapter
from app.admin_support.adm02_internal_http import (
    ADM02_INTERNAL_AUDIT_EVENTS_PATH,
    ADM02_INTERNAL_ENSURE_ACCESS_PATH,
)
from app.admin_support.adm02_postgres_ensure_access_audit_read_adapter import (
    Adm02PostgresEnsureAccessAuditReadAdapter,
)
from app.admin_support.adm02_wiring import (
    build_adm02_ensure_access_audit_lookup_handler,
    build_adm02_ensure_access_handler,
)
from app.internal_admin.adm01_bundle import (
    Adm01InternalLookupWithPostgresIssuanceStateDependencies,
    build_adm01_internal_lookup_starlette_app_with_postgres_issuance_state,
)
from app.internal_admin.adm01_http_config import (
    Adm01InternalHttpConfig,
    load_adm01_internal_http_config_from_env,
)
from app.internal_admin.adm02_mutation_opt_in_config import (
    load_adm02_ensure_access_opt_in_from_env,
)
from app.issuance.fake_provider import FakeIssuanceProvider, FakeProviderMode
from app.issuance.service import IssuanceService
from app.persistence.postgres_issuance_state import PostgresIssuanceStateRepository
from app.persistence.postgres_migrations_runtime import apply_slice1_postgres_migrations_from_runtime_config
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository
from app.security.config import ConfigurationError, RuntimeConfig, load_runtime_config

_ENV_ALLOWLIST = "ADM01_INTERNAL_HTTP_ALLOWLIST"

_STDOUT_DISABLED = "adm01_internal_http: disabled"
_STDERR_CONFIG = "adm01_internal_http: config_error"
_STDERR_FAILED = "adm01_internal_http: failed"


def _load_allowlist_principal_ids_from_env() -> tuple[str, ...]:
    raw = os.environ.get(_ENV_ALLOWLIST, "").strip()
    if not raw:
        msg = f"missing or empty configuration: {_ENV_ALLOWLIST}"
        raise ConfigurationError(msg)
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        msg = f"missing or empty configuration: {_ENV_ALLOWLIST}"
        raise ConfigurationError(msg)
    return tuple(parts)


async def _default_create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=4)


async def _run_uvicorn_app(app: object, *, host: str, port: int) -> None:
    """Run uvicorn until stopped. Intended to be monkeypatched in tests (no real socket when patched)."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        use_colors=False,
    )
    server = uvicorn.Server(config)
    await server.serve()


def _wire_adm02_ensure_access_route(
    *,
    app: Starlette,
    pool: asyncpg.Pool,
    repo: PostgresIssuanceStateRepository,
    identities: PostgresUserIdentityRepository,
    snapshots: PostgresSubscriptionSnapshotReader,
    allowlist: tuple[str, ...],
    mutation_opt_in_enabled: bool,
) -> None:
    ensure_access_handler = None
    if mutation_opt_in_enabled:
        ensure_access_handler = build_adm02_ensure_access_handler(
            identity=build_adm01_identity_resolve_from_postgres_user_identities(identities),
            subscription=build_adm01_subscription_read_from_postgres_snapshots(snapshots),
            issuance=build_adm01_issuance_read_from_postgres_issuance_state(repo),
            mutation=Adm02EnsureAccessIssuanceMutationAdapter(
                IssuanceService(
                    FakeIssuanceProvider(FakeProviderMode.SUCCESS),
                    operational_state=repo,
                )
            ),
            audit=FanoutAdm02EnsureAccessAuditSink(
                PostgresAdm02EnsureAccessAuditSink(pool),
                StructuredLoggingAdm02EnsureAccessAuditSink(),
            ),
            adm02_allowlisted_internal_admin_principal_ids=allowlist,
            adm02_mutation_opt_in_enabled=mutation_opt_in_enabled,
        )
    ensure_access_audit_lookup_handler = build_adm02_ensure_access_audit_lookup_handler(
        audit_read=Adm02PostgresEnsureAccessAuditReadAdapter(pool),
        adm02_allowlisted_internal_admin_principal_ids=allowlist,
    )
    from app.admin_support.adm02_internal_http import create_adm02_internal_http_app
    from app.admin_support.contracts import (
        Adm02BillingFactsCategory,
        Adm02BillingFactsDiagnostics,
        Adm02DiagnosticsInput,
        Adm02DiagnosticsOutcome,
        Adm02DiagnosticsResult,
        Adm02DiagnosticsSummary,
        Adm02QuarantineDiagnostics,
        Adm02QuarantineMarker,
        Adm02QuarantineReasonCode,
        Adm02ReconciliationDiagnostics,
        Adm02ReconciliationRunMarker,
        RedactionMarker,
    )
    from app.admin_support.principal_extraction import DefaultInternalAdminPrincipalExtractor

    class _NoopDiagnosticsHandler:
        async def handle(self, inp: Adm02DiagnosticsInput) -> Adm02DiagnosticsResult:
            return Adm02DiagnosticsResult(
                outcome=Adm02DiagnosticsOutcome.SUCCESS,
                correlation_id=inp.correlation_id,
                summary=Adm02DiagnosticsSummary(
                    billing=Adm02BillingFactsDiagnostics(
                        category=Adm02BillingFactsCategory.NONE,
                        internal_fact_refs=(),
                    ),
                    quarantine=Adm02QuarantineDiagnostics(
                        marker=Adm02QuarantineMarker.NONE,
                        reason_code=Adm02QuarantineReasonCode.NONE,
                    ),
                    reconciliation=Adm02ReconciliationDiagnostics(
                        last_run_marker=Adm02ReconciliationRunMarker.NONE,
                    ),
                    redaction=RedactionMarker.NONE,
                ),
            )

    adm02_app = create_adm02_internal_http_app(
        _NoopDiagnosticsHandler(),
        DefaultInternalAdminPrincipalExtractor(),
        ensure_access_handler=ensure_access_handler,
        ensure_access_audit_lookup_handler=ensure_access_audit_lookup_handler,
    )
    for route in list(adm02_app.routes):
        path = getattr(route, "path", None)
        if path in {ADM02_INTERNAL_ENSURE_ACCESS_PATH, ADM02_INTERNAL_AUDIT_EVENTS_PATH}:
            app.router.routes.append(route)


async def async_run_adm01_internal_http_from_env(
    *,
    load_adm01_config: Callable[[], Adm01InternalHttpConfig] = load_adm01_internal_http_config_from_env,
    load_adm02_mutation_opt_in: Callable[[], bool] = load_adm02_ensure_access_opt_in_from_env,
    load_runtime: Callable[[], RuntimeConfig] = load_runtime_config,
    apply_migrations: Callable[..., Awaitable[None]] = apply_slice1_postgres_migrations_from_runtime_config,
    create_pool: Callable[[str], Awaitable[asyncpg.Pool]] | None = None,
    build_app: Callable[
        [Adm01InternalLookupWithPostgresIssuanceStateDependencies],
        Any,
    ] = build_adm01_internal_lookup_starlette_app_with_postgres_issuance_state,
    run_uvicorn: Callable[..., Awaitable[None]] | None = None,
) -> int:
    """
    Load config from the process environment, optionally run migrations, pool, and uvicorn.

    Returns ``0`` on success or disabled; ``1`` on configuration or startup failure.
    """
    try:
        http_cfg = load_adm01_config()
    except ConfigurationError:
        print(_STDERR_CONFIG, file=sys.stderr, flush=True)
        return 1

    if not http_cfg.enabled:
        print(_STDOUT_DISABLED, flush=True)
        return 0

    pool_opener = create_pool if create_pool is not None else _default_create_pool
    uvicorn_runner = run_uvicorn if run_uvicorn is not None else _run_uvicorn_app
    try:
        adm02_mutation_opt_in_enabled = load_adm02_mutation_opt_in()
    except ConfigurationError:
        print(_STDERR_CONFIG, file=sys.stderr, flush=True)
        return 1

    try:
        rt = load_runtime()
    except ConfigurationError:
        print(_STDERR_CONFIG, file=sys.stderr, flush=True)
        return 1

    try:
        allowlist = _load_allowlist_principal_ids_from_env()
    except ConfigurationError:
        print(_STDERR_CONFIG, file=sys.stderr, flush=True)
        return 1

    try:
        await apply_migrations(rt)
    except ConfigurationError:
        print(_STDERR_CONFIG, file=sys.stderr, flush=True)
        return 1
    except Exception:
        print(_STDERR_FAILED, file=sys.stderr, flush=True)
        return 1

    dsn = (rt.database_url or "").strip()
    if not dsn:
        print(_STDERR_CONFIG, file=sys.stderr, flush=True)
        return 1

    pool: asyncpg.Pool | None = None
    try:
        try:
            pool = await pool_opener(dsn)
        except Exception:
            print(_STDERR_FAILED, file=sys.stderr, flush=True)
            return 1

        repo = PostgresIssuanceStateRepository(pool)
        identities = PostgresUserIdentityRepository(pool)
        snapshots = PostgresSubscriptionSnapshotReader(pool)
        deps = Adm01InternalLookupWithPostgresIssuanceStateDependencies(
            identity=build_adm01_identity_resolve_from_postgres_user_identities(identities),
            subscription=build_adm01_subscription_read_from_postgres_snapshots(snapshots),
            entitlement=build_adm01_entitlement_read_from_postgres_snapshots(snapshots),
            postgres_issuance_state=repo,
            policy=build_adm01_policy_read_from_postgres_snapshots(snapshots),
            redaction=None,
            adm01_allowlisted_internal_admin_principal_ids=allowlist,
        )
        app = build_app(deps)
        if isinstance(app, Starlette):
            _wire_adm02_ensure_access_route(
                app=app,
                pool=pool,
                repo=repo,
                identities=identities,
                snapshots=snapshots,
                allowlist=allowlist,
                mutation_opt_in_enabled=adm02_mutation_opt_in_enabled,
            )
        await uvicorn_runner(
            app,
            host=http_cfg.bind_host,
            port=http_cfg.bind_port,
        )
    except ConfigurationError:
        print(_STDERR_CONFIG, file=sys.stderr, flush=True)
        return 1
    except Exception:
        print(_STDERR_FAILED, file=sys.stderr, flush=True)
        return 1
    finally:
        if pool is not None:
            with contextlib.suppress(Exception):
                await pool.close()

    return 0


def main(argv: list[str] | None = None) -> int:
    _ = argv
    try:
        return asyncio.run(async_run_adm01_internal_http_from_env())
    except ConfigurationError:
        print(_STDERR_CONFIG, file=sys.stderr, flush=True)
        return 1
    except Exception:
        print(_STDERR_FAILED, file=sys.stderr, flush=True)
        return 1


__all__ = [
    "async_run_adm01_internal_http_from_env",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
