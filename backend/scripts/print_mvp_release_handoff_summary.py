"""Print a bounded, read-only MVP release handoff summary."""

from __future__ import annotations

from pathlib import Path

_REQUIRED_BACKEND_FILES = (
    "docs/mvp_release_artifact_manifest.md",
    "docs/mvp_release_readiness_runbook.md",
)
_REQUIRED_REPO_FILES = (
    ".github/workflows/backend-mvp-release-readiness.yml",
    ".github/workflows/backend-postgres-mvp-smoke-validation.yml",
)


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _repo_root(backend_dir: Path) -> Path:
    if (backend_dir / ".github").exists():
        return backend_dir
    return backend_dir.parent


def generate_handoff_summary(*, backend_dir: Path | None = None) -> tuple[bool, tuple[str, ...], str]:
    root = _backend_dir() if backend_dir is None else backend_dir
    repo_root = _repo_root(root)
    issues: list[str] = [
        "missing_release_handoff_source_doc" for rel_path in _REQUIRED_BACKEND_FILES if not (root / rel_path).exists()
    ]
    issues.extend(
        "missing_release_handoff_workflow" for rel_path in _REQUIRED_REPO_FILES if not (repo_root / rel_path).exists()
    )

    if issues:
        # Keep issue list deterministic and bounded.
        unique = tuple(dict.fromkeys(issues))
        return False, unique, ""

    lines = (
        "mvp_release_handoff_summary",
        "release_baseline_commands:",
        "- python scripts/run_mvp_release_readiness.py",
        "- python scripts/run_mvp_config_doctor.py --profile polling|webhook|internal-admin|retention|all",
        "- python scripts/run_postgres_mvp_smoke_local.py",
        "ci_gates:",
        "- backend-mvp-release-readiness",
        "- backend-postgres-mvp-smoke-validation",
        "manual_gates:",
        "- real operator config doctor profiles",
        "- local Docker smoke",
        "- deployed /healthz and /readyz",
        "- Telegram setWebhook and secret rotation",
        "- retention delete approval",
        "out_of_scope:",
        "- public billing ingress",
        "- real provider SDK",
        "- raw credential/config delivery",
        "- full production SLO/alerting certification",
    )
    return True, (), "\n".join(lines)


def main() -> int:
    ok, issues, summary = generate_handoff_summary()
    if ok:
        print(summary)
        print("mvp_release_handoff_summary: ok")
        return 0

    print("mvp_release_handoff_summary: fail")
    for issue in issues:
        print(f"issue_code={issue}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
