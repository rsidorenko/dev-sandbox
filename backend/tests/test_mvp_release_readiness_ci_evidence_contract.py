"""Contract checks for lightweight MVP release readiness CI workflow."""

from __future__ import annotations

from pathlib import Path

_WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "backend-mvp-release-readiness.yml"
_TRIGGER_PATH_MARKERS = (
    ".github/workflows/backend-mvp-release-readiness.yml",
    "PROJECT_HANDOFF.md",
    "backend/RELEASE_STATUS.md",
    "backend/docs/mvp_release_artifact_manifest.md",
    "backend/docs/mvp_release_readiness_runbook.md",
    "backend/docs/postgres_mvp_smoke_runbook.md",
    "backend/docs/telegram_access_resend_runbook.md",
    "backend/docs/admin_support_internal_read_gate_runbook.md",
    "backend/scripts/run_mvp_release_readiness.py",
    "backend/scripts/run_mvp_repo_release_health_check.py",
    "backend/scripts/run_mvp_release_checklist.py",
    "backend/scripts/run_mvp_release_preflight.py",
    "backend/scripts/run_mvp_config_doctor.py",
    "backend/scripts/print_mvp_release_handoff_summary.py",
    "backend/scripts/run_mvp_final_static_handoff_check.py",
    "backend/tests/test_*release*",
    "backend/tests/test_*handoff*",
    "backend/tests/test_project_handoff_contract.py",
)


def _workflow_text() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def test_release_readiness_workflow_exists() -> None:
    assert _WORKFLOW_PATH.exists()


def test_workflow_has_push_and_pull_request_with_paths_filters() -> None:
    text = _workflow_text()
    assert "pull_request:" in text
    assert "push:" in text
    assert "paths:" in text


def test_workflow_paths_cover_release_and_handoff_artifacts() -> None:
    text = _workflow_text()
    for marker in _TRIGGER_PATH_MARKERS:
        assert marker in text


def test_workflow_runs_repo_health_check_checklist_preflight_and_config_doctor_unit_tests() -> None:
    text = _workflow_text()
    assert "python scripts/run_mvp_repo_release_health_check.py" in text
    assert "python scripts/run_mvp_release_checklist.py" in text
    assert "python scripts/run_mvp_release_preflight.py" in text
    assert "python scripts/run_mvp_final_static_handoff_check.py" in text
    assert "python -m pytest -q tests/test_run_mvp_config_doctor.py" in text


def test_workflow_does_not_run_local_postgres_smoke_or_docker() -> None:
    lowered = _workflow_text().lower()
    assert "run_postgres_mvp_smoke_local.py" not in lowered
    assert "docker-compose" not in lowered
    assert "docker compose" not in lowered
    assert "services:" not in lowered


def test_workflow_does_not_reference_repo_secrets_or_real_env_gate_markers() -> None:
    lowered = _workflow_text().lower()
    assert "${{ secrets." not in lowered
    assert "database_url" not in lowered
    assert "run_mvp_config_doctor.py --profile all" not in lowered


def test_workflow_has_no_raw_production_like_dsn_host_or_token_markers() -> None:
    lowered = _workflow_text().lower()
    for forbidden in (
        "database_url=",
        "postgres://",
        "postgresql://",
        "rds.amazonaws.com",
        "prod-db",
        "bearer ",
        "private key",
        "begin ",
        "token=",
        "telegram_webhook_secret_token=",
    ):
        assert forbidden not in lowered
