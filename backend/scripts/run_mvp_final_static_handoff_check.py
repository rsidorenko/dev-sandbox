"""Run final lightweight static handoff checks for MVP release package."""

from __future__ import annotations

import subprocess
from pathlib import Path

_FORBIDDEN_OUTPUT_FRAGMENTS = (
    "DATABASE_URL=",
    "BOT_TOKEN=",
    "TELEGRAM_WEBHOOK_SECRET_TOKEN=",
    "ADM02_ENSURE_ACCESS_ENABLE=",
    "OPERATIONAL_RETENTION_DELETE_ENABLE=",
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
    "customer_ref",
    "provider_ref",
    "checkout_attempt_id",
    "internal_user_id",
)

_STAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "vless_key_lifecycle",
        ("python", "-m", "pytest", "-q", "tests/test_vless_key_lifecycle.py"),
    ),
    (
        "vless_provider",
        ("python", "-m", "pytest", "-q", "tests/test_vless_provider.py"),
    ),
    (
        "fulfillment_ingress",
        ("python", "-m", "pytest", "-q", "tests/test_payment_fulfillment_ingress.py"),
    ),
    (
        "repo_release_health_check",
        ("python", "scripts/run_mvp_repo_release_health_check.py"),
    ),
)


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _contains_forbidden_fragment(text: str) -> bool:
    lowered = text.lower()
    return any(fragment.lower() in lowered for fragment in _FORBIDDEN_OUTPUT_FRAGMENTS)


def run_final_static_handoff_check(*, runner=None) -> int:
    run = runner or subprocess.run
    backend_dir = _backend_dir()

    for stage, command in _STAGES:
        completed = run(list(command), cwd=backend_dir, check=False)
        if completed.returncode != 0:
            print("mvp_final_static_handoff_check: fail")
            print(f"stage={stage}")
            return 1

    print("mvp_final_static_handoff_check: ok")
    return 0


def main() -> int:
    exit_code = run_final_static_handoff_check()
    summary = "mvp_final_static_handoff_check: ok" if exit_code == 0 else "mvp_final_static_handoff_check: fail"
    if _contains_forbidden_fragment(summary):
        print("mvp_final_static_handoff_check: fail")
        print("stage=output_guard")
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
