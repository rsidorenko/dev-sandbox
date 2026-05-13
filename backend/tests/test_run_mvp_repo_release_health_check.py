"""Unit tests for static MVP repo release health check script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_mvp_repo_release_health_check.py"
_FORBIDDEN_FRAGMENTS = (
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


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_mvp_repo_release_health_check", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_valid_fixture(base_dir: Path) -> None:
    (base_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (base_dir / "docs").mkdir(parents=True, exist_ok=True)
    (base_dir / "tests").mkdir(parents=True, exist_ok=True)
    (base_dir / ".github" / "workflows").mkdir(parents=True, exist_ok=True)

    for rel in (
        "run_mvp_release_readiness.py",
        "run_mvp_release_checklist.py",
        "run_mvp_release_preflight.py",
        "run_mvp_config_doctor.py",
        "run_mvp_repo_release_health_check.py",
        "print_mvp_release_handoff_summary.py",
    ):
        (base_dir / "scripts" / rel).write_text("pass\n", encoding="utf-8")

    (base_dir / "docs" / "mvp_release_artifact_manifest.md").write_text(
        "\n".join(
            (
                "backend/RELEASE_STATUS.md",
                "python scripts/run_mvp_release_readiness.py",
                "python scripts/run_mvp_release_checklist.py",
                "python scripts/run_mvp_release_preflight.py",
                "python scripts/run_mvp_config_doctor.py --profile polling|webhook|internal-admin|retention|all",
                "python scripts/run_mvp_repo_release_health_check.py",
                "python scripts/print_mvp_release_handoff_summary.py",
                "backend-mvp-release-readiness",
                "backend-postgres-mvp-smoke-validation",
                "backend/docs/mvp_release_readiness_runbook.md",
                "docs/mvp_release_ci_trigger_decision.md",
                "tests/test_mvp_final_release_gate_contract.py",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (base_dir / "docs" / "mvp_release_readiness_runbook.md").write_text(
        "\n".join(
            (
                "python scripts/run_mvp_release_readiness.py",
                "python scripts/run_mvp_release_checklist.py",
                "python scripts/run_mvp_release_preflight.py",
                "python scripts/run_mvp_config_doctor.py --profile polling",
                "python scripts/run_mvp_repo_release_health_check.py",
                "backend/docs/mvp_release_artifact_manifest.md",
                "docs/mvp_release_ci_trigger_decision.md",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (base_dir / "docs" / "mvp_release_ci_trigger_decision.md").write_text(
        "\n".join(
            (
                "PROJECT_HANDOFF.md",
                "backend release/handoff docs/scripts/tests",
                "backend-mvp-release-readiness",
                "no Docker/local smoke",
                "no DB service and no `DATABASE_URL` gate",
                "no `${{ secrets.* }}`",
                "no real `run_mvp_config_doctor.py --profile all` gate",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (base_dir / "docs" / "mvp_release_staging_manifest.md").write_text(
        "\n".join(
            (
                "Purpose: manual staging guide for the release/handoff package only.",
                "do not run blanket `git add .`",
                ".cursor/plans/**",
                "backend/src/app/**",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (base_dir / "RELEASE_STATUS.md").write_text(
        "\n".join(
            (
                "docs/mvp_release_artifact_manifest.md",
                "docs/mvp_release_readiness_runbook.md",
                "python scripts/run_mvp_release_readiness.py",
                "backend-mvp-release-readiness",
                "backend-postgres-mvp-smoke-validation",
                "tests/test_mvp_final_release_gate_contract.py",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (base_dir / ".github" / "workflows" / "backend-mvp-release-readiness.yml").write_text(
        "\n".join(
            (
                "name: backend-mvp-release-readiness",
                "on:",
                "  pull_request:",
                "    paths:",
                '      - "PROJECT_HANDOFF.md"',
                '      - "backend/RELEASE_STATUS.md"',
                '      - "backend/docs/mvp_release_artifact_manifest.md"',
                '      - "backend/scripts/run_mvp_final_static_handoff_check.py"',
                '      - "backend/tests/test_project_handoff_contract.py"',
                "jobs:",
                "  mvp-release-readiness:",
                "    steps:",
                "      - run: python scripts/run_mvp_repo_release_health_check.py",
                "      - run: python scripts/run_mvp_release_checklist.py",
                "      - run: python scripts/run_mvp_release_preflight.py",
                "      - run: python scripts/run_mvp_final_static_handoff_check.py",
                "      - run: python -m pytest -q tests/test_run_mvp_config_doctor.py",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (base_dir / ".github" / "workflows" / "backend-postgres-mvp-smoke-validation.yml").write_text(
        "name: backend-postgres-mvp-smoke-validation\n",
        encoding="utf-8",
    )
    for rel in (
        "test_mvp_release_artifact_manifest_contract.py",
        "test_mvp_release_staging_manifest_contract.py",
        "test_mvp_release_readiness_runbook_contract.py",
        "test_mvp_release_ci_trigger_decision_contract.py",
        "test_mvp_release_readiness_workflow_structure_contract.py",
        "test_run_mvp_release_checklist.py",
        "test_print_mvp_release_handoff_summary.py",
        "test_release_status_contract.py",
        "test_mvp_final_release_gate_contract.py",
    ):
        (base_dir / "tests" / rel).write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")


def test_success_path_with_temp_fixture(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "?? .cursor/plans/example.plan.md\n",
    )

    assert ok is True
    assert issues == ()


def test_missing_required_artifact_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "scripts" / "run_mvp_release_preflight.py").unlink()

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_required_release_script" in issues


def test_missing_handoff_script_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "scripts" / "print_mvp_release_handoff_summary.py").unlink()

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_required_release_script" in issues


def test_missing_release_status_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "RELEASE_STATUS.md").unlink()

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_release_status_doc" in issues


def test_missing_release_status_contract_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "tests" / "test_release_status_contract.py").unlink()

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_release_status_contract_test" in issues


def test_missing_ci_trigger_decision_doc_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "docs" / "mvp_release_ci_trigger_decision.md").unlink()

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_ci_trigger_decision_doc" in issues


def test_missing_ci_trigger_decision_contract_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "tests" / "test_mvp_release_ci_trigger_decision_contract.py").unlink()

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_ci_trigger_decision_contract_test" in issues


def test_missing_final_release_gate_test_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "tests" / "test_mvp_final_release_gate_contract.py").unlink()

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_final_release_gate_contract_test" in issues


def test_missing_final_release_gate_marker_in_status_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "RELEASE_STATUS.md").write_text(
        "\n".join(
            (
                "docs/mvp_release_artifact_manifest.md",
                "docs/mvp_release_readiness_runbook.md",
                "python scripts/run_mvp_release_readiness.py",
                "backend-mvp-release-readiness",
                "backend-postgres-mvp-smoke-validation",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_release_status_final_release_gate_reference" in issues


def test_missing_final_release_gate_marker_in_manifest_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "docs" / "mvp_release_artifact_manifest.md").write_text(
        "\n".join(
            (
                "backend/RELEASE_STATUS.md",
                "python scripts/run_mvp_release_readiness.py",
                "python scripts/run_mvp_release_checklist.py",
                "python scripts/run_mvp_release_preflight.py",
                "python scripts/run_mvp_config_doctor.py --profile polling|webhook|internal-admin|retention|all",
                "python scripts/run_mvp_repo_release_health_check.py",
                "python scripts/print_mvp_release_handoff_summary.py",
                "backend-mvp-release-readiness",
                "backend-postgres-mvp-smoke-validation",
                "backend/docs/mvp_release_readiness_runbook.md",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_manifest_final_release_gate_reference" in issues


def test_missing_manifest_ci_trigger_decision_link_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "docs" / "mvp_release_artifact_manifest.md").write_text(
        "\n".join(
            (
                "backend/RELEASE_STATUS.md",
                "python scripts/run_mvp_release_readiness.py",
                "python scripts/run_mvp_release_checklist.py",
                "python scripts/run_mvp_release_preflight.py",
                "python scripts/run_mvp_config_doctor.py --profile polling|webhook|internal-admin|retention|all",
                "python scripts/run_mvp_repo_release_health_check.py",
                "python scripts/print_mvp_release_handoff_summary.py",
                "backend-mvp-release-readiness",
                "backend-postgres-mvp-smoke-validation",
                "backend/docs/mvp_release_readiness_runbook.md",
                "tests/test_mvp_final_release_gate_contract.py",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_manifest_ci_trigger_decision_reference" in issues


def test_missing_runbook_ci_trigger_decision_link_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "docs" / "mvp_release_readiness_runbook.md").write_text(
        "\n".join(
            (
                "python scripts/run_mvp_release_readiness.py",
                "python scripts/run_mvp_release_checklist.py",
                "python scripts/run_mvp_release_preflight.py",
                "python scripts/run_mvp_config_doctor.py --profile polling",
                "python scripts/run_mvp_repo_release_health_check.py",
                "backend/docs/mvp_release_artifact_manifest.md",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_runbook_ci_trigger_decision_reference" in issues


def test_missing_ci_trigger_decision_marker_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "docs" / "mvp_release_ci_trigger_decision.md").write_text(
        "\n".join(
            (
                "PROJECT_HANDOFF.md",
                "backend release/handoff docs/scripts/tests",
                "backend-mvp-release-readiness",
                "no Docker/local smoke",
                "no DB service and no `DATABASE_URL` gate",
                "no `${{ secrets.* }}`",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_ci_trigger_decision_marker" in issues


def test_missing_manifest_release_status_reference_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "docs" / "mvp_release_artifact_manifest.md").write_text(
        "\n".join(
            (
                "python scripts/run_mvp_release_readiness.py",
                "python scripts/run_mvp_release_checklist.py",
                "python scripts/run_mvp_release_preflight.py",
                "python scripts/run_mvp_config_doctor.py --profile polling|webhook|internal-admin|retention|all",
                "python scripts/run_mvp_repo_release_health_check.py",
                "python scripts/print_mvp_release_handoff_summary.py",
                "backend-mvp-release-readiness",
                "backend-postgres-mvp-smoke-validation",
                "backend/docs/mvp_release_readiness_runbook.md",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_manifest_release_status_reference" in issues


def test_missing_release_status_marker_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "RELEASE_STATUS.md").write_text(
        "\n".join(
            (
                "docs/mvp_release_artifact_manifest.md",
                "docs/mvp_release_readiness_runbook.md",
                "python scripts/run_mvp_release_readiness.py",
                "backend-mvp-release-readiness",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_release_status_marker" in issues


def test_missing_handoff_manifest_marker_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "docs" / "mvp_release_artifact_manifest.md").write_text(
        "\n".join(
            (
                "python scripts/run_mvp_release_readiness.py",
                "python scripts/run_mvp_release_checklist.py",
                "python scripts/run_mvp_release_preflight.py",
                "python scripts/run_mvp_config_doctor.py --profile polling|webhook|internal-admin|retention|all",
                "python scripts/run_mvp_repo_release_health_check.py",
                "backend-mvp-release-readiness",
                "backend-postgres-mvp-smoke-validation",
                "backend/docs/mvp_release_readiness_runbook.md",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_handoff_summary_manifest_reference" in issues


def test_missing_project_handoff_path_marker_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "backend-mvp-release-readiness.yml").write_text(
        "\n".join(
            (
                "name: backend-mvp-release-readiness",
                "on:",
                "  pull_request:",
                "    paths:",
                '      - "backend/RELEASE_STATUS.md"',
                '      - "backend/docs/mvp_release_artifact_manifest.md"',
                '      - "backend/scripts/run_mvp_final_static_handoff_check.py"',
                '      - "backend/tests/test_project_handoff_contract.py"',
                "jobs:",
                "  mvp-release-readiness:",
                "    steps:",
                "      - run: python scripts/run_mvp_repo_release_health_check.py",
                "      - run: python scripts/run_mvp_release_checklist.py",
                "      - run: python scripts/run_mvp_release_preflight.py",
                "      - run: python scripts/run_mvp_final_static_handoff_check.py",
                "      - run: python -m pytest -q tests/test_run_mvp_config_doctor.py",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_lightweight_workflow_path_marker" in issues


def test_missing_final_static_handoff_step_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "backend-mvp-release-readiness.yml").write_text(
        "\n".join(
            (
                "name: backend-mvp-release-readiness",
                "on:",
                "  pull_request:",
                "    paths:",
                '      - "PROJECT_HANDOFF.md"',
                '      - "backend/RELEASE_STATUS.md"',
                '      - "backend/docs/mvp_release_artifact_manifest.md"',
                '      - "backend/scripts/run_mvp_final_static_handoff_check.py"',
                '      - "backend/tests/test_project_handoff_contract.py"',
                "jobs:",
                "  mvp-release-readiness:",
                "    steps:",
                "      - run: python scripts/run_mvp_repo_release_health_check.py",
                "      - run: python scripts/run_mvp_release_checklist.py",
                "      - run: python scripts/run_mvp_release_preflight.py",
                "      - run: python -m pytest -q tests/test_run_mvp_config_doctor.py",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "missing_lightweight_workflow_command_marker" in issues


def test_forbidden_fragment_in_release_doc_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "docs" / "mvp_release_artifact_manifest.md").write_text(
        "DATABASE_URL=\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "forbidden_release_doc_fragment" in issues
    for issue in issues:
        assert "DATABASE_URL=" not in issue


def test_secrets_reference_in_workflow_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "backend-mvp-release-readiness.yml").write_text(
        "\n".join(
            (
                "name: backend-mvp-release-readiness",
                "on:",
                "  pull_request:",
                "    paths:",
                '      - "PROJECT_HANDOFF.md"',
                '      - "backend/RELEASE_STATUS.md"',
                '      - "backend/docs/mvp_release_artifact_manifest.md"',
                '      - "backend/scripts/run_mvp_final_static_handoff_check.py"',
                '      - "backend/tests/test_project_handoff_contract.py"',
                "jobs:",
                "  mvp-release-readiness:",
                "    steps:",
                "      - run: python scripts/run_mvp_repo_release_health_check.py",
                "      - run: python scripts/run_mvp_release_checklist.py",
                "      - run: python scripts/run_mvp_release_preflight.py",
                "      - run: python scripts/run_mvp_final_static_handoff_check.py",
                "      - run: python -m pytest -q tests/test_run_mvp_config_doctor.py",
                "${{ secrets.MVP_TOKEN }}",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "forbidden_release_workflow_fragment" in issues
    for issue in issues:
        assert "${{ secrets." not in issue


def test_database_url_in_workflow_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "backend-mvp-release-readiness.yml").write_text(
        "\n".join(
            (
                "name: backend-mvp-release-readiness",
                "on:",
                "  pull_request:",
                "    paths:",
                '      - "PROJECT_HANDOFF.md"',
                '      - "backend/RELEASE_STATUS.md"',
                '      - "backend/docs/mvp_release_artifact_manifest.md"',
                '      - "backend/scripts/run_mvp_final_static_handoff_check.py"',
                '      - "backend/tests/test_project_handoff_contract.py"',
                "jobs:",
                "  mvp-release-readiness:",
                "    steps:",
                "      - run: python scripts/run_mvp_repo_release_health_check.py",
                "      - run: python scripts/run_mvp_release_checklist.py",
                "      - run: python scripts/run_mvp_release_preflight.py",
                "      - run: python scripts/run_mvp_final_static_handoff_check.py",
                "      - run: python -m pytest -q tests/test_run_mvp_config_doctor.py",
                "      - run: export DATABASE_URL=postgresql://masked",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "",
    )

    assert ok is False
    assert "forbidden_release_workflow_fragment" in issues
    for issue in issues:
        assert "DATABASE_URL=" not in issue


def test_staged_deletion_cursor_plan_does_not_trigger_issue(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "D  .cursor/plans/removed.plan.md\n",
    )

    assert "tracked_cursor_plan_file" not in issues
    assert ok is True


def test_tracked_cursor_plan_status_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)

    ok, issues = script.run_repo_release_health_check(
        backend_dir=tmp_path,
        git_status_reader=lambda _repo_root: "A  .cursor/plans/some.plan.md\n",
    )

    assert ok is False
    assert "tracked_cursor_plan_file" in issues


def test_script_does_not_run_handoff_preflight_checklist_config_doctor_or_docker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    calls: list[tuple[tuple[str, ...], Path]] = []

    class _Completed:
        stdout = "?? .cursor/plans/example.plan.md\n"
        stderr = ""

    def _fake_run(
        command: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> _Completed:
        calls.append((tuple(command), cwd))
        return _Completed()

    monkeypatch.setattr(script.subprocess, "run", _fake_run)

    ok, issues = script.run_repo_release_health_check(backend_dir=tmp_path)

    assert ok is True
    assert issues == ()
    assert calls == [
        (
            ("git", "status", "--short", "--untracked-files=all"),
            tmp_path,
        )
    ]


def test_output_has_no_forbidden_fragments(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()

    monkeypatch.setattr(
        script,
        "run_repo_release_health_check",
        lambda: (False, ("forbidden_release_doc_fragment", "tracked_cursor_plan_file")),
    )

    _ = script.main()

    captured = capsys.readouterr()
    blob = (captured.out + captured.err).lower()
    for forbidden in _FORBIDDEN_FRAGMENTS:
        assert forbidden.lower() not in blob
