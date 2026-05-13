"""Structural contract checks for MVP release readiness workflow YAML shape."""

from __future__ import annotations

from pathlib import Path

_WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "backend-mvp-release-readiness.yml"
_REQUIRED_TRIGGER_PATHS = (
    "PROJECT_HANDOFF.md",
    "backend/RELEASE_STATUS.md",
    "backend/docs/mvp_release_artifact_manifest.md",
    "backend/docs/mvp_release_readiness_runbook.md",
    "backend/scripts/run_mvp_repo_release_health_check.py",
    "backend/scripts/run_mvp_release_checklist.py",
    "backend/scripts/run_mvp_release_preflight.py",
    "backend/scripts/run_mvp_config_doctor.py",
    "backend/scripts/run_mvp_final_static_handoff_check.py",
)
_REQUIRED_STEP_COMMANDS = (
    "python scripts/run_mvp_repo_release_health_check.py",
    "python scripts/run_mvp_release_checklist.py",
    "python scripts/run_mvp_release_preflight.py",
    "python scripts/run_mvp_final_static_handoff_check.py",
    "python -m pytest -q tests/test_run_mvp_config_doctor.py",
)
_FORBIDDEN_FRAGMENTS = (
    "${{ secrets.",
    "DATABASE_URL",
    "services:",
    "docker compose",
    "docker-compose",
    "run_postgres_mvp_smoke_local.py",
    "run_mvp_config_doctor.py --profile all",
)
_TEST_WILDCARD_PATHS = (
    "backend/tests/test_*release*",
    "backend/tests/test_*handoff*",
)
_TEST_EXPLICIT_FALLBACK_PATHS = (
    "backend/tests/test_mvp_release_readiness_ci_evidence_contract.py",
    "backend/tests/test_mvp_release_package_complete_contract.py",
    "backend/tests/test_mvp_final_release_gate_contract.py",
    "backend/tests/test_run_mvp_repo_release_health_check.py",
)


def _read_workflow() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _find_mapping_section(lines: list[str], key: str, *, parent_indent: int) -> tuple[int, int]:
    target = f"{key}:"
    for start, line in enumerate(lines):
        if _indent(line) != parent_indent:
            continue
        if line.strip() != target:
            continue
        end = len(lines)
        for idx in range(start + 1, len(lines)):
            stripped = lines[idx].strip()
            if not stripped:
                continue
            if _indent(lines[idx]) <= parent_indent:
                end = idx
                break
        return start, end
    raise AssertionError(f"missing mapping section: {key}")


def _extract_paths_from_trigger(lines: list[str], trigger: str) -> tuple[str, ...]:
    _, on_end = _find_mapping_section(lines, "on", parent_indent=0)
    on_start, _ = _find_mapping_section(lines, "on", parent_indent=0)
    on_block = lines[on_start + 1 : on_end]
    trigger_start, trigger_end = _find_mapping_section(on_block, trigger, parent_indent=2)
    trigger_block = on_block[trigger_start + 1 : trigger_end]

    paths_start = None
    for idx, line in enumerate(trigger_block):
        if _indent(line) == 4 and line.strip() == "paths:":
            paths_start = idx
            break
    assert paths_start is not None, f"missing paths under trigger: {trigger}"

    paths: list[str] = []
    for line in trigger_block[paths_start + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if _indent(line) <= 4:
            break
        if stripped.startswith("- "):
            paths.append(stripped[2:].strip().strip('"').strip("'"))
    assert paths, f"empty paths under trigger: {trigger}"
    return tuple(paths)


def test_release_readiness_workflow_file_exists() -> None:
    assert _WORKFLOW_PATH.exists()


def test_workflow_on_trigger_contains_push_and_pull_request_with_paths() -> None:
    lines = _read_workflow().splitlines()
    pull_request_paths = _extract_paths_from_trigger(lines, "pull_request")
    push_paths = _extract_paths_from_trigger(lines, "push")
    assert pull_request_paths
    assert push_paths


def test_workflow_trigger_paths_cover_required_release_and_test_scope() -> None:
    lines = _read_workflow().splitlines()
    pull_request_paths = set(_extract_paths_from_trigger(lines, "pull_request"))
    push_paths = set(_extract_paths_from_trigger(lines, "push"))

    for required in _REQUIRED_TRIGGER_PATHS:
        assert required in pull_request_paths
        assert required in push_paths

    has_pattern_or_explicit_pull = any(
        marker in pull_request_paths for marker in (*_TEST_WILDCARD_PATHS, *_TEST_EXPLICIT_FALLBACK_PATHS)
    )
    has_pattern_or_explicit_push = any(
        marker in push_paths for marker in (*_TEST_WILDCARD_PATHS, *_TEST_EXPLICIT_FALLBACK_PATHS)
    )
    assert has_pattern_or_explicit_pull
    assert has_pattern_or_explicit_push


def test_workflow_job_shape_and_expected_steps_are_present() -> None:
    lines = _read_workflow().splitlines()
    jobs_start, jobs_end = _find_mapping_section(lines, "jobs", parent_indent=0)
    jobs_block = lines[jobs_start + 1 : jobs_end]

    assert any(_indent(line) == 2 and line.strip().endswith(":") for line in jobs_block)
    assert any(line.strip() == "mvp-release-readiness:" for line in jobs_block)

    text = _read_workflow()
    # This workflow relies on backend defaults; allow a fallback "cd backend" style.
    assert "working-directory: backend" in text or "cd backend &&" in text
    for command in _REQUIRED_STEP_COMMANDS:
        assert command in text


def test_workflow_keeps_lightweight_security_boundaries() -> None:
    text = _read_workflow()
    lowered = text.lower()
    for fragment in _FORBIDDEN_FRAGMENTS:
        if fragment == "DATABASE_URL":
            assert fragment.lower() not in lowered
            continue
        assert fragment.lower() not in lowered
