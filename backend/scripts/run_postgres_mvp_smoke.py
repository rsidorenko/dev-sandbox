"""Minimal helper to smoke-check PostgreSQL MVP happy-path."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_MUTATING_TESTS_GUARD_ENV = "SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS"
_TRUTHY_ENV_VALUES = {"1", "true", "yes"}


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _is_env_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY_ENV_VALUES


def _require_mutating_tests_opt_in() -> None:
    if _is_env_truthy(os.environ.get(_MUTATING_TESTS_GUARD_ENV)):
        return
    raise RuntimeError(f"{_MUTATING_TESTS_GUARD_ENV} must be explicitly set for isolated/dev DB smoke runs")


def _build_child_env() -> dict[str, str]:
    raw_database_url = os.environ.get("DATABASE_URL", "").strip()
    if not raw_database_url:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL MVP smoke run")

    child_env = os.environ.copy()
    child_env["SLICE1_USE_POSTGRES_REPOS"] = "1"
    child_env["BILLING_NORMALIZED_INGEST_ENABLE"] = "1"
    child_env["BILLING_SUBSCRIPTION_APPLY_ENABLE"] = "1"
    child_env["ISSUANCE_OPERATOR_ENABLE"] = "1"
    child_env["TELEGRAM_ACCESS_RESEND_ENABLE"] = "1"
    child_env["ADM02_ENSURE_ACCESS_ENABLE"] = "1"
    # Canonical smoke defaults: used by customer journey + reconcile checks.
    # Operator can override via env, but CI/local smoke should be able to run
    # with safe defaults when unset.
    child_env.setdefault("ACCESS_RECONCILE_MAX_INTERVAL_SECONDS", "3600")
    child_env.setdefault("SUBSCRIPTION_DEFAULT_PERIOD_DAYS", "30")
    if not child_env.get("BOT_TOKEN"):
        child_env["BOT_TOKEN"] = "1234567890tok"
    return child_env


def _operator_billing_subprocess_env(full_child_env: dict[str, str]) -> dict[str, str]:
    """Child env for ``check_operator_billing_ingest_apply_e2e``: full smoke env minus slice-1 feature flags.

    The billing script only reads ``DATABASE_URL`` and billing opt-ins, but the subprocess still
    inherits runner/packaging variables from the parent env. We drop issuance/ADM-02/Telegram/repo
    toggles so billing is not coupled to those code paths while keeping the rest of ``child_env``.
    """
    out = {str(k): str(v) for k, v in full_child_env.items()}
    for key in (
        "ISSUANCE_OPERATOR_ENABLE",
        "TELEGRAM_ACCESS_RESEND_ENABLE",
        "ADM02_ENSURE_ACCESS_ENABLE",
        "SLICE1_USE_POSTGRES_REPOS",
    ):
        out.pop(key, None)
    out["BILLING_NORMALIZED_INGEST_ENABLE"] = "1"
    out["BILLING_SUBSCRIPTION_APPLY_ENABLE"] = "1"
    return out


def main() -> None:
    _require_mutating_tests_opt_in()
    child_env = _build_child_env()
    backend_dir = _backend_dir()

    subprocess.run(
        ["python", "-m", "app.persistence"],
        cwd=backend_dir,
        env=child_env,
        check=True,
    )
    subprocess.run(
        ["python", "scripts/run_slice1_retention_dry_run.py"],
        cwd=backend_dir,
        env=child_env,
        check=True,
    )
    subprocess.run(
        ["python", "scripts/check_operator_billing_ingest_apply_e2e.py"],
        cwd=backend_dir,
        env=_operator_billing_subprocess_env(child_env),
        check=True,
    )
    subprocess.run(
        ["python", "scripts/check_postgres_mvp_access_fulfillment_e2e.py"],
        cwd=backend_dir,
        env=child_env,
        check=True,
    )
    subprocess.run(
        ["python", "scripts/check_customer_journey_e2e.py"],
        cwd=backend_dir,
        env=child_env,
        check=True,
    )
    subprocess.run(
        ["python", "scripts/reconcile_expired_access.py"],
        cwd=backend_dir,
        env=child_env,
        check=True,
    )
    subprocess.run(
        ["python", "scripts/check_reconcile_health.py"],
        cwd=backend_dir,
        env=child_env,
        check=True,
    )
    subprocess.run(
        [
            "pytest",
            "-q",
            "tests/test_postgres_slice1_process_env_async.py",
            "tests/test_postgres_migration_ledger_integration.py",
        ],
        cwd=backend_dir,
        env=child_env,
        check=True,
    )


if __name__ == "__main__":
    main()
