"""Точка входа оператора: один нормализованный JSON-факт -> ledger + аудит (без публичного HTTP, без сырых нагрузок провайдера).

Запуск: ``BILLING_NORMALIZED_INGEST_ENABLE=1`` и ``python -m app.application.billing_ingestion_main --input-file <путь>``
(опционально ``--input-file -`` для stdin).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path
import asyncpg

from app.application.billing_ingestion import NormalizedBillingFactInput
from app.persistence.billing_events_ledger_contracts import (
    BillingEventAmountCurrency,
    BillingEventLedgerStatus,
)
from app.persistence.postgres_billing_ingestion_atomic import PostgresAtomicBillingIngestion
from app.security.config import ConfigurationError, load_runtime_config
from app.security.errors import PersistenceDependencyError
from app.security.validation import ValidationError

# Явное согласие оператора: без истинного значения точка входа завершается без доступа к БД.
BILLING_NORMALIZED_INGEST_ENABLE = "BILLING_NORMALIZED_INGEST_ENABLE"

# Схема 1: только нормализованные скаляры (должно совпадать с :class:`NormalizedBillingFactInput`).
_SCHEMA_VERSION = 1
_JSON_KEYS_ALLOWED = frozenset(
    {
        "schema_version",
        "billing_provider_key",
        "external_event_id",
        "event_type",
        "event_effective_at",
        "event_received_at",
        "status",
        "ingestion_correlation_id",
        "internal_fact_ref",
        "internal_user_id",
        "checkout_attempt_id",
        "amount_currency",
    }
)
_AM_KEYS = frozenset({"amount_minor_units", "currency_code"})

_STDERR_FAIL = "billing_normalized_ingest: failed"
_STDOUT_OK_PREFIX = "billing_normalized_ingest: ok"
_CATEGORY_KEY = "category"


def _ingest_enable_truthy() -> bool:
    raw = os.environ.get(BILLING_NORMALIZED_INGEST_ENABLE, "").strip().lower()
    return raw in ("1", "true", "yes")


def _read_input_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    p = Path(path)
    with p.open(encoding="utf-8") as f:
        return f.read()


def _parse_timestamptz(*, name: str, raw: object) -> datetime:
    if not isinstance(raw, str):
        raise ValidationError(f"{name} must be a string")
    s = raw.strip()
    if not s:
        raise ValidationError(f"{name} is required")
    iso = s
    if iso.endswith("Z") or iso.endswith("z"):
        iso = iso[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValidationError(f"{name} is not a valid ISO-8601 datetime") from exc
    if dt.tzinfo is None:
        raise ValidationError(f"{name} must include a timezone offset")
    return dt


def _parse_status(raw: object) -> BillingEventLedgerStatus:
    if not isinstance(raw, str):
        raise ValidationError("status must be a string")
    s = raw.strip()
    for candidate in BillingEventLedgerStatus:
        if candidate.value == s:
            return candidate
    raise ValidationError("status must be one of: accepted, duplicate, ignored")


def _parse_amount_currency(raw: object | None) -> BillingEventAmountCurrency | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError("amount_currency must be an object or null")
    if set(raw) - _AM_KEYS:
        raise ValidationError("amount_currency has unknown fields")
    amu = raw.get("amount_minor_units", None)
    cur = raw.get("currency_code", None)
    if amu is not None and (not isinstance(amu, int) or isinstance(amu, bool) or amu < 0):
        raise ValidationError("amount_minor_units must be a non-negative integer or null")
    if cur is not None and (not isinstance(cur, str) or not cur.strip()):
        raise ValidationError("currency_code must be a non-empty string or null")
    return BillingEventAmountCurrency(
        amount_minor_units=amu,
        currency_code=cur.strip() if isinstance(cur, str) else None,
    )


def _require_non_empty_str(data: dict[str, object], key: str) -> str:
    v = data.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValidationError(f"{key} must be a non-empty string")
    return v.strip()


def parse_json_to_normalized_billing_input(raw: str) -> NormalizedBillingFactInput:
    """Разбирает строгий JSON (схема 1) в :class:`NormalizedBillingFactInput` (для тестов/CLI)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError("input is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValidationError("JSON root must be an object")
    extra = set(data) - _JSON_KEYS_ALLOWED
    if extra:
        raise ValidationError("unknown or disallowed JSON fields in input")
    for required in (
        "schema_version",
        "billing_provider_key",
        "external_event_id",
        "event_type",
        "event_effective_at",
        "event_received_at",
        "status",
        "ingestion_correlation_id",
    ):
        if required not in data:
            raise ValidationError(f"missing field: {required}")
    if data.get("schema_version") != _SCHEMA_VERSION:
        raise ValidationError("schema_version must be 1 for this entrypoint")
    t_eff = _parse_timestamptz(name="event_effective_at", raw=data["event_effective_at"])
    t_rec = _parse_timestamptz(name="event_received_at", raw=data["event_received_at"])
    bpk = _require_non_empty_str(data, "billing_provider_key")
    ext = _require_non_empty_str(data, "external_event_id")
    ev_type = _require_non_empty_str(data, "event_type")
    ing_corr = _require_non_empty_str(data, "ingestion_correlation_id")
    st = _parse_status(data["status"])
    amt = _parse_amount_currency(data.get("amount_currency"))
    internal_ref = data.get("internal_fact_ref", None)
    if internal_ref is not None and not isinstance(internal_ref, str):
        raise ValidationError("internal_fact_ref must be a string or null")
    if isinstance(internal_ref, str) and not internal_ref.strip():
        internal_ref = None
    if internal_ref is not None and not str(internal_ref).strip():
        raise ValidationError("internal_fact_ref must be non-empty when set")
    uid = data.get("internal_user_id", None)
    if uid is not None and (not isinstance(uid, str) or not uid.strip()):
        raise ValidationError("internal_user_id must be a non-empty string or null")
    caid = data.get("checkout_attempt_id", None)
    if caid is not None and (not isinstance(caid, str) or not caid.strip()):
        raise ValidationError("checkout_attempt_id must be a non-empty string or null")
    ref_final = internal_ref.strip() if isinstance(internal_ref, str) and internal_ref.strip() else None
    return NormalizedBillingFactInput(
        billing_provider_key=bpk,
        external_event_id=ext,
        event_type=ev_type,
        event_effective_at=t_eff,
        event_received_at=t_rec,
        status=st,
        ingestion_correlation_id=ing_corr,
        internal_user_id=uid if isinstance(uid, str) else None,
        checkout_attempt_id=caid if isinstance(caid, str) else None,
        amount_currency=amt,
        internal_fact_ref=ref_final,
    )


def _err_category(exc: BaseException) -> str:
    if isinstance(exc, ValidationError):
        return "validation"
    if isinstance(exc, ConfigurationError):
        return "config"
    if isinstance(exc, PersistenceDependencyError):
        return "persistence"
    if isinstance(exc, (OSError, asyncpg.PostgresError, TimeoutError)):
        return "persistence"
    return "internal_error"


def _stderr_fail(category: str) -> None:
    # Фиксированная форма; без текста исключения (может содержать DSN/PII во вложенных причинах).
    print(
        f"{_STDERR_FAIL} {_CATEGORY_KEY}={category}",
        file=sys.stderr,
        flush=True,
    )


def _print_ok_summary(
    *,
    internal_fact_ref: str,
    outcome: str,
    status: str,
    correlation_id: str,
) -> None:
    print(
        f"{_STDOUT_OK_PREFIX}"
        f" internal_fact_ref={internal_fact_ref}"
        f" outcome={outcome}"
        f" status={status}"
        f" correlation_id={correlation_id}",
        flush=True,
    )


async def _default_open_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=4)


def _ingest_outcome_label(is_idempotent_replay: bool) -> str:
    return "idempotent_replay" if is_idempotent_replay else "accepted"


OpenPoolFn = Callable[[str], Awaitable[asyncpg.Pool]]


async def async_run_billing_ingest_from_parsed(
    input_: NormalizedBillingFactInput,
    *,
    dsn: str,
    open_pool: OpenPoolFn | None = None,
) -> tuple[str, str, str, str]:
    """Загружает один факт в одной транзакции Postgres; возвращает (outcome, ref, status, correlation_id)."""
    open_fn: OpenPoolFn = open_pool if open_pool is not None else _default_open_pool
    pool = await open_fn(dsn)
    try:
        atomic = PostgresAtomicBillingIngestion(pool)
        result = await atomic.ingest_normalized_billing_fact(input_)
        r = result.record
        out_label = _ingest_outcome_label(result.is_idempotent_replay)
        return (out_label, r.internal_fact_ref, r.status.value, r.ingestion_correlation_id)
    finally:
        await pool.close()


def main() -> None:
    """CLI-точка входа: ``python -m app.application.billing_ingestion_main``."""
    raise SystemExit(asyncio.run(async_main()))


async def async_main(argv: Sequence[str] | None = None) -> int:
    """Запускает загрузку оператора; возвращает код завершения процесса (0 ok, 1 ошибка)."""
    qargv = list(sys.argv[1:]) if argv is None else list(argv)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--input-file",
        required=True,
        metavar="PATH",
        help="Path to JSON (schema 1) or - for stdin",
    )
    try:
        ns = p.parse_args(qargv)
    except SystemExit as e:
        # argparse: 0 для -h/--help; ненулевое для некорректных аргументов.
        c = e.code
        if c in (0, None):
            return 0
        return 1
    in_path: str = ns.input_file
    if not _ingest_enable_truthy():
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
        raw_text = _read_input_text(in_path)
    except OSError:
        _stderr_fail("io")
        return 1
    try:
        parsed = parse_json_to_normalized_billing_input(raw_text)
    except ValidationError:
        _stderr_fail("validation")
        return 1
    try:
        out, ref, st, cid = await async_run_billing_ingest_from_parsed(parsed, dsn=dsn)
    except (ValidationError, ConfigurationError) as exc:
        _stderr_fail(_err_category(exc))
        return 1
    except (PersistenceDependencyError, OSError, asyncpg.PostgresError, TimeoutError) as exc:
        _stderr_fail(_err_category(exc))
        return 1
    except Exception:
        _stderr_fail("internal_error")
        return 1
    _print_ok_summary(
        internal_fact_ref=ref,
        outcome=out,
        status=st,
        correlation_id=cid,
    )
    return 0


if __name__ == "__main__":
    main()
