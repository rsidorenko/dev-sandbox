"""Точка входа оператора: применить существующий загруженный биллинг-факт к подписке (UC-05, Postgres атомарно).

Запуск: ``BILLING_SUBSCRIPTION_APPLY_ENABLE=1`` и
``python -m app.application.billing_subscription_apply_main --internal-fact-ref <ref>`` (из ``backend/``).

Без публичного HTTP, без сырых нагрузок провайдера, без автоматической связи с биллинг-загрузкой.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Sequence

import asyncpg

from app.application.apply_billing_subscription import (
    ApplyAcceptedBillingFactHandler,
    ApplyAcceptedBillingFactInput,
    ApplyAcceptedBillingFactResult,
)
from app.persistence.postgres_billing_subscription_apply import PostgresAtomicUC05SubscriptionApply
from app.security.config import ConfigurationError, load_runtime_config
from app.security.errors import PersistenceDependencyError
from app.security.validation import ValidationError, validate_internal_fact_ref_uc05
from app.shared.types import OperationOutcomeCategory

BILLING_SUBSCRIPTION_APPLY_ENABLE = "BILLING_SUBSCRIPTION_APPLY_ENABLE"

_STDERR_FAIL = "billing_subscription_apply: failed"
_STDOUT_OK_PREFIX = "billing_subscription_apply: ok"
_CATEGORY_KEY = "category"


def _apply_enable_truthy() -> bool:
    raw = os.environ.get(BILLING_SUBSCRIPTION_APPLY_ENABLE, "").strip().lower()
    return raw in ("1", "true", "yes")


def _err_category(exc: BaseException) -> str:
    if isinstance(exc, ValidationError):
        return "validation"
    if isinstance(exc, ConfigurationError):
        return "config"
    if isinstance(exc, PersistenceDependencyError):
        return "persistence"
    if isinstance(exc, OSError | asyncpg.PostgresError | TimeoutError):
        return "persistence"
    return "domain"


def _stderr_fail(category: str) -> None:
    print(
        f"{_STDERR_FAIL} {_CATEGORY_KEY}={category}",
        file=sys.stderr,
        flush=True,
    )


def _outcome_state_labels(res: ApplyAcceptedBillingFactResult) -> tuple[str, str]:
    """(значение категории операции, исход apply или литерал none)."""
    out = res.operation_outcome.value
    st = res.apply_outcome.value if res.apply_outcome is not None else "none"
    return (out, st)


def _print_ok_summary(
    *,
    internal_fact_ref: str,
    operation_outcome: str,
    state_label: str,
) -> None:
    print(
        f"{_STDOUT_OK_PREFIX} internal_fact_ref={internal_fact_ref} outcome={operation_outcome} state={state_label}",
        flush=True,
    )


def _category_for_apply_result(res: ApplyAcceptedBillingFactResult) -> str:
    o = res.operation_outcome
    if o is OperationOutcomeCategory.NOT_FOUND:
        return "not_found"
    if o is OperationOutcomeCategory.VALIDATION_FAILURE:
        return "validation"
    if o in (OperationOutcomeCategory.SUCCESS, OperationOutcomeCategory.IDEMPOTENT_NOOP):
        return ""
    if o in (OperationOutcomeCategory.RETRYABLE_DEPENDENCY, OperationOutcomeCategory.INTERNAL_FAILURE):
        return "persistence"
    return "domain"


async def _default_open_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=4)


OpenPoolFn = Callable[[str], Awaitable[asyncpg.Pool]]


async def async_run_apply(
    internal_fact_ref: str,
    *,
    dsn: str,
    open_pool: OpenPoolFn | None = None,
) -> ApplyAcceptedBillingFactResult:
    """Запускает UC-05 в одном жизненном цикле пула; делегирует :class:`ApplyAcceptedBillingFactHandler`."""
    open_fn: OpenPoolFn = open_pool if open_pool is not None else _default_open_pool
    pool = await open_fn(dsn)
    try:
        apply_pg = PostgresAtomicUC05SubscriptionApply(pool)
        handler = ApplyAcceptedBillingFactHandler(apply_pg)
        return await handler.handle(ApplyAcceptedBillingFactInput(internal_fact_ref=internal_fact_ref))
    finally:
        await pool.close()


def _parse_ref_arg(raw: str) -> str:
    """Очищает и валидирует ``internal_fact_ref`` (без управляющих символов, ограниченный)."""
    return validate_internal_fact_ref_uc05(raw)


def main() -> None:
    """CLI: ``python -m app.application.billing_subscription_apply_main``."""
    raise SystemExit(asyncio.run(async_main()))


async def async_main(argv: Sequence[str] | None = None) -> int:
    """Запускает UC-05 apply оператора; код завершения 0 при успехе или идемпотентном повторе."""
    qargv = list(sys.argv[1:]) if argv is None else list(argv)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--internal-fact-ref",
        required=True,
        metavar="REF",
        help="Internal fact ref from an already ingested billing_events_ledger row",
    )
    try:
        ns = p.parse_args(qargv)
    except SystemExit as e:
        c = e.code
        if c in (0, None):
            return 0
        return 1

    if not _apply_enable_truthy():
        _stderr_fail("opt_in")
        return 1
    try:
        config = load_runtime_config()
        dsn = (config.database_url or "").strip()
        if not dsn:
            raise ConfigurationError("missing or empty configuration: DATABASE_URL")
    except ConfigurationError:
        _stderr_fail("config")
        return 1
    try:
        ref = _parse_ref_arg(ns.internal_fact_ref)
    except ValidationError:
        _stderr_fail("validation")
        return 1
    try:
        res = await async_run_apply(ref, dsn=dsn)
    except (ValidationError, ConfigurationError) as exc:
        _stderr_fail(_err_category(exc))
        return 1
    except (PersistenceDependencyError, OSError, asyncpg.PostgresError, TimeoutError) as exc:
        _stderr_fail(_err_category(exc))
        return 1
    except Exception:
        _stderr_fail("domain")
        return 1
    if res.operation_outcome in (OperationOutcomeCategory.SUCCESS, OperationOutcomeCategory.IDEMPOTENT_NOOP):
        if _safety_deny_zero_exit(res):
            _stderr_fail("domain")
            return 1
        op_v, st_v = _outcome_state_labels(res)
        _print_ok_summary(
            internal_fact_ref=ref,
            operation_outcome=op_v,
            state_label=st_v,
        )
        return 0
    _stderr_fail(_category_for_apply_result(res) or "domain")
    return 1


def _safety_deny_zero_exit(res: ApplyAcceptedBillingFactResult) -> bool:
    """Если исход заявляет успех, но нагрузка apply отсутствует где требуется, не завершать с кодом 0."""
    if res.operation_outcome is OperationOutcomeCategory.IDEMPOTENT_NOOP and res.apply_outcome is None:
        return True
    return bool(res.operation_outcome is OperationOutcomeCategory.SUCCESS and res.apply_outcome is None)
