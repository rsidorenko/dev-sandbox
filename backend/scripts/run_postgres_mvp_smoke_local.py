"""Run PostgreSQL MVP smoke against isolated local Docker PostgreSQL."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

_LOCAL_KEEP_ON_FAILURE_ENV = "SLICE1_POSTGRES_MVP_SMOKE_LOCAL_KEEP_ON_FAILURE"
_MUTATING_TESTS_GUARD_ENV = "SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS"
_TRUTHY_ENV_VALUES = {"1", "true", "yes"}
_DEFAULT_READINESS_TIMEOUT_SECONDS = 30.0
_DEFAULT_READINESS_INTERVAL_SECONDS = 1.0
_Runner = Callable[..., subprocess.CompletedProcess[str]]


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _compose_file() -> Path:
    return _backend_dir() / "docker-compose.postgres-smoke.yml"


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY_ENV_VALUES


def _run_checked(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None,
    runner: _Runner,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return runner(
        list(command),
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def _detect_compose_command(*, cwd: Path, runner: _Runner) -> list[str]:
    _run_checked(["docker", "--version"], cwd=cwd, env=None, runner=runner)

    for candidate in (["docker", "compose"], ["docker-compose"]):
        try:
            _run_checked([*candidate, "version"], cwd=cwd, env=None, runner=runner)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    raise RuntimeError("Docker Compose is not available")


def _resolve_mapped_port(
    compose_cmd: Sequence[str],
    *,
    project_name: str,
    compose_path: Path,
    cwd: Path,
    runner: _Runner,
) -> str:
    result = _run_checked(
        [*compose_cmd, "-p", project_name, "-f", str(compose_path), "port", "postgres", "5432"],
        cwd=cwd,
        env=None,
        runner=runner,
        capture_output=True,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Could not resolve mapped local Postgres port")
    endpoint = lines[-1]
    if ":" not in endpoint:
        raise RuntimeError("Unexpected docker compose port output")
    return endpoint.rsplit(":", 1)[-1]


def _build_smoke_env(parent_env: Mapping[str, str], *, host_port: str) -> dict[str, str]:
    child_env = dict(parent_env)
    child_env["DATABASE_URL"] = f"postgresql://postgres:postgres@127.0.0.1:{host_port}/postgres_smoke_local"
    child_env[_MUTATING_TESTS_GUARD_ENV] = "1"
    return child_env


def _sanitize_error_message(error: Exception) -> str:
    """Return a compact safe error message without traceback or secret-like values."""
    message = str(error).strip() or error.__class__.__name__
    lowered = message.lower()
    redaction_markers = ("postgresql://", "password", "token", "secret")
    if any(marker in lowered for marker in redaction_markers):
        return "sensitive details redacted"
    return message


def _wait_for_postgres_ready(
    compose_cmd: Sequence[str],
    *,
    project_name: str,
    compose_path: Path,
    cwd: Path,
    runner: _Runner,
    timeout_seconds: float = _DEFAULT_READINESS_TIMEOUT_SECONDS,
    interval_seconds: float = _DEFAULT_READINESS_INTERVAL_SECONDS,
) -> None:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    deadline = time.monotonic() + timeout_seconds
    readiness_command = [
        *compose_cmd,
        "-p",
        project_name,
        "-f",
        str(compose_path),
        "exec",
        "-T",
        "postgres",
        "pg_isready",
        "-U",
        "postgres",
        "-d",
        "postgres_smoke_local",
    ]
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            _run_checked(readiness_command, cwd=cwd, env=None, runner=runner)
            return
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            last_error = exc
            time.sleep(interval_seconds)

    raise RuntimeError("Local PostgreSQL readiness check timed out") from last_error


def _down_command(compose_cmd: Sequence[str], *, project_name: str, compose_path: Path) -> list[str]:
    return [*compose_cmd, "-p", project_name, "-f", str(compose_path), "down", "--volumes", "--remove-orphans"]


def main(argv: Sequence[str] | None = None, *, runner: _Runner = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-on-failure",
        action="store_true",
        help="Keep local smoke container/volume after a failure for troubleshooting.",
    )
    args = parser.parse_args(argv)

    backend_dir = _backend_dir()
    compose_path = _compose_file()
    if not compose_path.exists():
        raise RuntimeError("Missing docker-compose.postgres-smoke.yml")

    compose_cmd = _detect_compose_command(cwd=backend_dir, runner=runner)
    keep_on_failure = args.keep_on_failure or _is_truthy(os.environ.get(_LOCAL_KEEP_ON_FAILURE_ENV))
    project_name = f"slice1-smoke-{uuid.uuid4().hex[:10]}"
    smoke_failed = False

    try:
        _run_checked(
            [*compose_cmd, "-p", project_name, "-f", str(compose_path), "up", "-d", "postgres"],
            cwd=backend_dir,
            env=None,
            runner=runner,
        )
        local_port = _resolve_mapped_port(
            compose_cmd,
            project_name=project_name,
            compose_path=compose_path,
            cwd=backend_dir,
            runner=runner,
        )
        _wait_for_postgres_ready(
            compose_cmd,
            project_name=project_name,
            compose_path=compose_path,
            cwd=backend_dir,
            runner=runner,
        )
        smoke_env = _build_smoke_env(os.environ, host_port=local_port)
        _run_checked(
            ["python", "scripts/run_postgres_mvp_smoke.py"],
            cwd=backend_dir,
            env=smoke_env,
            runner=runner,
        )
    except Exception:
        smoke_failed = True
        raise
    finally:
        if smoke_failed and keep_on_failure:
            print("Local smoke failed; keeping isolated containers for inspection.")
        else:
            try:
                _run_checked(
                    _down_command(compose_cmd, project_name=project_name, compose_path=compose_path),
                    cwd=backend_dir,
                    env=None,
                    runner=runner,
                )
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("Warning: local smoke cleanup failed; run docker compose down manually for this project.")

    return 0


def _run_cli(argv: Sequence[str] | None = None) -> int:
    try:
        return main(argv)
    except Exception as exc:
        safe_message = _sanitize_error_message(exc)
        print(f"Local Docker smoke gate failed: {safe_message}", file=sys.stderr)
        print(
            "Action: verify Docker is running, no local Postgres port conflict, then rerun "
            "`python scripts/run_postgres_mvp_smoke_local.py`.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(_run_cli())
