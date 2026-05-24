"""Static, read-only repository health check for MVP release package."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

_REQUIRED_SCRIPTS = (
    "scripts/run_mvp_release_readiness.py",
    "scripts/run_mvp_release_checklist.py",
    "scripts/run_mvp_release_preflight.py",
    "scripts/run_mvp_config_doctor.py",
    "scripts/run_mvp_repo_release_health_check.py",
    "scripts/print_mvp_release_handoff_summary.py",
)
_REQUIRED_LIGHTWEIGHT_WORKFLOW_PATH_MARKERS = (
    "PROJECT_HANDOFF.md",
    "backend/RELEASE_STATUS.md",
    "backend/docs/mvp_release_artifact_manifest.md",
    "backend/scripts/run_mvp_final_static_handoff_check.py",
)
_REQUIRED_LIGHTWEIGHT_WORKFLOW_COMMAND_MARKERS = (
    "python scripts/run_mvp_repo_release_health_check.py",
    "python scripts/run_mvp_release_checklist.py",
    "python scripts/run_mvp_release_preflight.py",
    "python scripts/run_mvp_final_static_handoff_check.py",
    "python -m pytest -q tests/test_run_mvp_config_doctor.py",
)
_REQUIRED_DOCS = (
    "docs/mvp_release_artifact_manifest.md",
    "docs/mvp_release_readiness_runbook.md",
    "docs/mvp_release_ci_trigger_decision.md",
    "docs/mvp_release_staging_manifest.md",
    "RELEASE_STATUS.md",
)
_REQUIRED_WORKFLOWS = (
    ".github/workflows/backend-mvp-release-readiness.yml",
    ".github/workflows/backend-postgres-mvp-smoke-validation.yml",
)
_REQUIRED_CONTRACT_TESTS = (
    "tests/test_vless_key_lifecycle.py",
    "tests/test_vless_provider.py",
    "tests/test_payment_fulfillment_ingress.py",
)
_SCRIPT_REFERENCE_MARKERS = (
    "python scripts/run_mvp_release_readiness.py",
    "python scripts/run_mvp_release_checklist.py",
    "python scripts/run_mvp_release_preflight.py",
    "python scripts/run_mvp_config_doctor.py",
    "python scripts/run_mvp_repo_release_health_check.py",
    "python scripts/print_mvp_release_handoff_summary.py",
)
_WORKFLOW_REFERENCE_MARKERS = (
    "backend-mvp-release-readiness",
    "backend-postgres-mvp-smoke-validation",
)
_RELEASE_STATUS_MARKERS = (
    "docs/mvp_release_artifact_manifest.md",
    "docs/mvp_release_readiness_runbook.md",
    "python scripts/run_mvp_release_readiness.py",
    "backend-mvp-release-readiness",
    "backend-postgres-mvp-smoke-validation",
)
_DECISION_NOTE_REQUIRED_MARKERS = (
    "PROJECT_HANDOFF.md",
    "backend release/handoff docs/scripts/tests",
    "backend-mvp-release-readiness",
    "no Docker",
    "no DB",
    "no `${{ secrets.* }}`",
    "run_mvp_config_doctor.py --profile all",
)
_FORBIDDEN_RELEASE_FRAGMENTS = (
    "DATABASE_URL=",
    "BOT_TOKEN=",
    "TELEGRAM_WEBHOOK_SECRET_TOKEN=",
    "postgres://",
    "postgresql://",
    "provider_issuance_ref",
    "issue_idempotency_key",
)
_FORBIDDEN_LIGHTWEIGHT_WORKFLOW_FRAGMENTS = (
    "${{ secrets.",
    "database_url",
    "services:",
    "docker-compose",
    "docker compose",
    "run_postgres_mvp_smoke_local.py",
    "run_mvp_config_doctor.py --profile all",
)
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


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _repo_root(backend_dir: Path) -> Path:
    if (backend_dir / ".github").exists():
        return backend_dir
    return backend_dir.parent


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_git_status(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return (completed.stdout or "") + (completed.stderr or "")


def _check_forbidden_output(issues: Sequence[str]) -> bool:
    payload = " ".join(issues)
    return any(fragment in payload for fragment in _FORBIDDEN_OUTPUT_FRAGMENTS)


def run_repo_release_health_check(
    *,
    backend_dir: Path | None = None,
    git_status_reader: Callable[[Path], str] = _run_git_status,
) -> tuple[bool, tuple[str, ...]]:
    root = _backend_dir() if backend_dir is None else backend_dir
    repo_root = _repo_root(root)
    issues: list[str] = []
    seen: set[str] = set()

    def _append_issue(code: str) -> None:
        if code not in seen:
            issues.append(code)
            seen.add(code)

    for rel_path in _REQUIRED_SCRIPTS:
        if not (root / rel_path).exists():
            _append_issue("missing_required_release_script")

    for rel_path in _REQUIRED_DOCS:
        if not (root / rel_path).exists():
            if rel_path == "RELEASE_STATUS.md":
                _append_issue("missing_release_status_doc")
            elif rel_path == "docs/mvp_release_ci_trigger_decision.md":
                _append_issue("missing_ci_trigger_decision_doc")
            else:
                _append_issue("missing_required_release_doc")

    for rel_path in _REQUIRED_WORKFLOWS:
        if not (repo_root / rel_path).exists():
            _append_issue("missing_required_release_workflow")

    for rel_path in _REQUIRED_CONTRACT_TESTS:
        if not (root / rel_path).exists():
            _append_issue("missing_required_release_contract_test")

    manifest_path = root / "docs/mvp_release_artifact_manifest.md"
    runbook_path = root / "docs/mvp_release_readiness_runbook.md"
    decision_note_path = root / "docs/mvp_release_ci_trigger_decision.md"
    release_status_path = root / "RELEASE_STATUS.md"
    lightweight_workflow_path = repo_root / ".github/workflows/backend-mvp-release-readiness.yml"
    manifest_text = _read_text(manifest_path) if manifest_path.exists() else ""
    runbook_text = _read_text(runbook_path) if runbook_path.exists() else ""
    decision_note_text = _read_text(decision_note_path) if decision_note_path.exists() else ""
    release_status_text = _read_text(release_status_path) if release_status_path.exists() else ""
    lightweight_workflow_text = _read_text(lightweight_workflow_path) if lightweight_workflow_path.exists() else ""

    if "backend/RELEASE_STATUS.md" not in manifest_text:
        _append_issue("missing_manifest_release_status_reference")
    if "docs/mvp_release_ci_trigger_decision.md" not in manifest_text:
        _append_issue("missing_manifest_ci_trigger_decision_reference")
    if "docs/mvp_release_ci_trigger_decision.md" not in runbook_text:
        _append_issue("missing_runbook_ci_trigger_decision_reference")

    for marker in _DECISION_NOTE_REQUIRED_MARKERS:
        if marker not in decision_note_text:
            _append_issue("missing_ci_trigger_decision_marker")
            break

    for marker in _SCRIPT_REFERENCE_MARKERS:
        if marker not in manifest_text and marker not in runbook_text:
            _append_issue("missing_release_script_reference")
            break

    if "python scripts/print_mvp_release_handoff_summary.py" not in manifest_text:
        _append_issue("missing_handoff_summary_manifest_reference")

    if (
        "backend/docs/mvp_release_readiness_runbook.md" not in manifest_text
        or "backend/docs/mvp_release_artifact_manifest.md" not in runbook_text
    ):
        _append_issue("missing_release_docs_cross_link")

    for marker in _WORKFLOW_REFERENCE_MARKERS:
        if marker not in manifest_text:
            _append_issue("missing_release_workflow_reference")
            break

    for marker in _RELEASE_STATUS_MARKERS:
        if marker not in release_status_text:
            _append_issue("missing_release_status_marker")
            break

    for marker in _REQUIRED_LIGHTWEIGHT_WORKFLOW_PATH_MARKERS:
        if marker not in lightweight_workflow_text:
            _append_issue("missing_lightweight_workflow_path_marker")
            break

    for marker in _REQUIRED_LIGHTWEIGHT_WORKFLOW_COMMAND_MARKERS:
        if marker not in lightweight_workflow_text:
            _append_issue("missing_lightweight_workflow_command_marker")
            break

    lowered_lightweight_workflow = lightweight_workflow_text.lower()
    for fragment in _FORBIDDEN_LIGHTWEIGHT_WORKFLOW_FRAGMENTS:
        if fragment.lower() in lowered_lightweight_workflow:
            _append_issue("forbidden_release_workflow_fragment")
            break

    release_text_targets = (
        manifest_text,
        runbook_text,
        lightweight_workflow_text,
    )
    lowered_targets = tuple(body.lower() for body in release_text_targets)
    for fragment in _FORBIDDEN_RELEASE_FRAGMENTS:
        if fragment.lower() in lowered_targets[0] or fragment.lower() in lowered_targets[1]:
            _append_issue("forbidden_release_doc_fragment")
            break
        if fragment.lower() in lowered_targets[2]:
            _append_issue("forbidden_release_workflow_fragment")
            break

    status_blob = git_status_reader(repo_root)
    for line in status_blob.splitlines():
        normalized = line.replace("\\", "/")
        if ".cursor/plans/" not in normalized:
            continue
        status_prefix = normalized[:2]
        if status_prefix[0] == "D":
            continue
        if status_prefix != "??":
            _append_issue("tracked_cursor_plan_file")
            break

    if _check_forbidden_output(issues):
        return False, ("unsafe_issue_code_payload",)
    return len(issues) == 0, tuple(issues)


def main() -> int:
    ok, issues = run_repo_release_health_check()
    if ok:
        print("mvp_repo_release_health_check: ok")
        return 0

    print("mvp_repo_release_health_check: fail")
    for issue in issues:
        print(f"issue_code={issue}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
