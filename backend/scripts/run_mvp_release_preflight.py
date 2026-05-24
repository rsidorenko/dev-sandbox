"""Run lightweight MVP release preflight over targeted pytest contracts."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

_FORBIDDEN_OUTPUT_FRAGMENTS = (
    "database_url",
    "postgres://",
    "postgresql://",
    "bearer ",
    "private key",
    "begin ",
    "token=",
    "vpn://",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "schema_version",
    "customer_ref",
    "provider_ref",
    "checkout_attempt_id",
    "internal_user_id",
    "telegram_webhook_secret_token=",
)


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _preflight_groups() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return (
        (
            "canonical_smoke_contracts",
            (
                "tests/test_run_postgres_mvp_smoke.py",
                "tests/test_run_postgres_mvp_access_fulfillment_e2e.py",
                "tests/test_vless_key_lifecycle.py",
            ),
        ),
        (
            "telegram_runtime_hardening",
            (
                "tests/test_telegram_webhook_ingress.py",
                "tests/test_telegram_webhook_main.py",
                "tests/test_bot_transport_dispatcher.py",
                "tests/test_bootstrap_composition.py",
                "tests/test_telegram_command_rate_limit.py",
                "tests/test_telegram_update_dedup.py",
            ),
        ),
        (
            "admin_support_audit",
            (
                "tests/test_adm01_internal_http_main.py",
                "tests/test_adm02_internal_http.py",
                "tests/test_adm02_ensure_access_audit_logging_sink.py",
                "tests/test_adm02_ensure_access_audit_read_endpoint.py",
                "tests/test_adm02_ensure_access_audit_read_adapter.py",
                "tests/test_adm02_ensure_access_postgres_audit_sink.py",
            ),
        ),
        (
            "retention_migrations",
            (
                "tests/test_run_slice1_retention_dry_run.py",
                "tests/test_postgres_migrations.py",
                "tests/test_postgres_migration_ledger_integration.py",
            ),
        ),
    )


def _build_pytest_command(targets: Sequence[str]) -> list[str]:
    return ["python", "-m", "pytest", "-q", *targets]


def _contains_forbidden_fragment(text: str) -> bool:
    lowered = text.lower()
    return any(fragment in lowered for fragment in _FORBIDDEN_OUTPUT_FRAGMENTS)


def run_preflight(*, runner=None) -> int:
    backend_dir = _backend_dir()
    run = runner or subprocess.run
    for _group_name, targets in _preflight_groups():
        command = _build_pytest_command(targets)
        completed = run(command, cwd=backend_dir, check=False)
        if completed.returncode != 0:
            print("mvp_release_preflight: fail")
            return 1
    print("mvp_release_preflight: ok")
    return 0


def main() -> int:
    exit_code = run_preflight()
    # Guardrail: wrapper itself should never emit obviously sensitive markers.
    summary_line = "mvp_release_preflight: ok" if exit_code == 0 else "mvp_release_preflight: fail"
    if _contains_forbidden_fragment(summary_line):
        print("mvp_release_preflight: fail")
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
