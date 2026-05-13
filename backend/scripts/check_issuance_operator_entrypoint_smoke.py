"""Operator smoke for ``python -m app.application.issuance_operator_main``.

Covers disabled and enabled-with-missing-config paths without DB/network.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN_OUTPUT_FRAGMENTS = (
    "DATABASE_URL",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "provider_issuance_ref",
    "PRIVATE KEY",
)
_DISABLED_LINE = "issuance_operator: failed category=opt_in"
_CONFIG_ERROR_LINE = "issuance_operator: failed category=config"


def _run_entrypoint_with_env(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "app.application.issuance_operator_main",
            "issue",
            "--internal-user-id",
            "smoke-user",
            "--access-profile-key",
            "smoke-profile",
            "--issue-idempotency-key",
            "smoke-issue-idem",
        ],
        cwd=str(_BACKEND_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _assert_no_forbidden_output(text: str) -> None:
    upper_text = text.upper()
    for frag in _FORBIDDEN_OUTPUT_FRAGMENTS:
        if frag.upper() in upper_text:
            raise RuntimeError("entrypoint smoke output leak guard failed")


def _base_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    src_path = str(_BACKEND_ROOT / "src")
    old_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_path if not old_pythonpath else f"{src_path}{os.pathsep}{old_pythonpath}"
    return env


def _disabled_check_env() -> dict[str, str]:
    env = _base_subprocess_env()
    env.pop("ISSUANCE_OPERATOR_ENABLE", None)
    env.pop("DATABASE_URL", None)
    env.pop("BOT_TOKEN", None)
    return env


def _config_error_check_env() -> dict[str, str]:
    env = _base_subprocess_env()
    env["ISSUANCE_OPERATOR_ENABLE"] = "1"
    env.pop("BOT_TOKEN", None)
    env.pop("DATABASE_URL", None)
    return env


def run_issuance_operator_entrypoint_smoke(
    runner: Callable[[dict[str, str]], subprocess.CompletedProcess[str]] = _run_entrypoint_with_env,
) -> None:
    disabled = runner(_disabled_check_env())
    disabled_combined = f"{disabled.stdout}{disabled.stderr}"
    _assert_no_forbidden_output(disabled_combined)
    if disabled.returncode == 0:
        raise RuntimeError("disabled path returned zero")
    if disabled.stdout.strip():
        raise RuntimeError("disabled path stdout must be empty")
    if disabled.stderr.strip() != _DISABLED_LINE:
        raise RuntimeError("disabled path stderr mismatch")

    config_error = runner(_config_error_check_env())
    config_combined = f"{config_error.stdout}{config_error.stderr}"
    _assert_no_forbidden_output(config_combined)
    if config_error.returncode == 0:
        raise RuntimeError("config-error path returned zero")
    if config_error.stdout.strip():
        raise RuntimeError("config-error path stdout must be empty")
    if config_error.stderr.strip() != _CONFIG_ERROR_LINE:
        raise RuntimeError("config-error stderr mismatch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        run_issuance_operator_entrypoint_smoke()
    except RuntimeError:
        print("issuance_operator_entrypoint_smoke: fail", file=sys.stderr, flush=True)
        return 1
    except Exception:
        print("issuance_operator_entrypoint_smoke: failed", file=sys.stderr, flush=True)
        return 1
    print("issuance_operator_entrypoint_smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
