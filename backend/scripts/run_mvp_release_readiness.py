"""Local MVP release readiness orchestrator (safe by default)."""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path

_ALLOWED_CONFIG_PROFILES = ("polling", "webhook", "internal-admin", "retention", "all")


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_stage(command: Sequence[str]) -> None:
    subprocess.run(command, cwd=_backend_dir(), check=True)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local MVP release readiness checks (safe default path).")
    parser.add_argument(
        "--config-profile",
        choices=_ALLOWED_CONFIG_PROFILES,
        default=None,
        help="Optionally run config doctor profile after checklist/preflight.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip release preflight stage (checklist still runs).",
    )
    return parser.parse_args(argv)


def run_release_readiness(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    stages: list[tuple[str, list[str]]] = [
        ("repo_release_health_check", ["python", "scripts/run_mvp_repo_release_health_check.py"]),
        ("checklist", ["python", "scripts/run_mvp_release_checklist.py"]),
    ]
    if not args.skip_preflight:
        stages.append(("preflight", ["python", "scripts/run_mvp_release_preflight.py"]))
    if args.config_profile is not None:
        stages.append(
            (
                "config_doctor",
                [
                    "python",
                    "scripts/run_mvp_config_doctor.py",
                    "--profile",
                    args.config_profile,
                ],
            )
        )

    for stage_name, command in stages:
        try:
            _run_stage(command)
        except subprocess.CalledProcessError as exc:
            print("mvp_release_readiness: fail")
            print(f"stage={stage_name}")
            return int(exc.returncode) if exc.returncode != 0 else 1

    print("mvp_release_readiness: ok")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_release_readiness(argv)


if __name__ == "__main__":
    raise SystemExit(main())
