"""Operator smoke for ``python -m app.internal_admin`` (disabled/config-error only; CI blocking gate).

Runs safe subprocess checks with no listener startup and no DB writes. Stdout is a single fixed
line on success; stderr uses fixed lines on expected/ unexpected failures.
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
    "issue_idempotency_key",
    "PRIVATE KEY",
)
_DISABLED_LINE = "adm01_internal_http: disabled"
_CONFIG_ERROR_LINE = "adm01_internal_http: config_error"


def _run_entrypoint_with_env(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.internal_admin"],
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
    env.pop("ADM01_INTERNAL_HTTP_ENABLE", None)
    env.pop("ADM01_INTERNAL_HTTP_ALLOWLIST", None)
    return env


def _config_error_check_env() -> dict[str, str]:
    # Intentionally invalid enabled config: missing allowlist must fail before startup/listener.
    env = _base_subprocess_env()
    env["ADM01_INTERNAL_HTTP_ENABLE"] = "1"
    env["BOT_TOKEN"] = "adm01-entry-smoke-token"
    env["APP_ENV"] = "development"
    env["DATABASE_URL"] = "postgresql://localhost/adm01_entry_smoke"
    env.pop("ADM01_INTERNAL_HTTP_ALLOWLIST", None)
    return env


def run_adm01_internal_http_entrypoint_smoke(
    runner: Callable[[dict[str, str]], subprocess.CompletedProcess[str]] = _run_entrypoint_with_env,
) -> None:
    disabled = runner(_disabled_check_env())
    disabled_combined = f"{disabled.stdout}{disabled.stderr}"
    _assert_no_forbidden_output(disabled_combined)
    if disabled.returncode != 0:
        raise RuntimeError("disabled path returned non-zero")
    if disabled.stdout.strip() != _DISABLED_LINE:
        raise RuntimeError("disabled path output mismatch")
    if disabled.stderr.strip():
        raise RuntimeError("disabled path stderr must be empty")

    config_error = runner(_config_error_check_env())
    config_combined = f"{config_error.stdout}{config_error.stderr}"
    _assert_no_forbidden_output(config_combined)
    if config_error.returncode == 0:
        raise RuntimeError("config-error path returned zero")
    if config_error.stderr.strip() != _CONFIG_ERROR_LINE:
        raise RuntimeError("config-error stderr mismatch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        run_adm01_internal_http_entrypoint_smoke()
    except RuntimeError:
        print("adm01_internal_http_entrypoint_smoke: fail", file=sys.stderr, flush=True)
        return 1
    except Exception:
        print("adm01_internal_http_entrypoint_smoke: failed", file=sys.stderr, flush=True)
        return 1
    print("adm01_internal_http_entrypoint_smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
