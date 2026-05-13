"""Unit tests for final static handoff check script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_mvp_final_static_handoff_check.py"
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
    spec = importlib.util.spec_from_file_location("run_mvp_final_static_handoff_check", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stage_order_and_commands_and_backend_cwd() -> None:
    script = _load_script_module()
    calls: list[tuple[tuple[str, ...], Path]] = []

    class _Completed:
        returncode = 0

    def _fake_run(command: list[str], *, cwd: Path, check: bool) -> _Completed:
        calls.append((tuple(command), cwd))
        return _Completed()

    exit_code = script.run_final_static_handoff_check(runner=_fake_run)

    assert exit_code == 0
    assert calls == [
        (
            ("python", "-m", "pytest", "-q", "tests/test_project_handoff_contract.py"),
            script._backend_dir(),
        ),
        (
            ("python", "-m", "pytest", "-q", "tests/test_release_status_contract.py"),
            script._backend_dir(),
        ),
        (
            ("python", "-m", "pytest", "-q", "tests/test_mvp_final_release_gate_contract.py"),
            script._backend_dir(),
        ),
        (
            ("python", "-m", "pytest", "-q", "tests/test_mvp_release_package_complete_contract.py"),
            script._backend_dir(),
        ),
        (
            (
                "python",
                "-m",
                "pytest",
                "-q",
                "tests/test_mvp_release_readiness_workflow_structure_contract.py",
            ),
            script._backend_dir(),
        ),
        (
            (
                "python",
                "-m",
                "pytest",
                "-q",
                "tests/test_mvp_release_staging_manifest_contract.py",
            ),
            script._backend_dir(),
        ),
        (
            ("python", "scripts/run_mvp_repo_release_health_check.py"),
            script._backend_dir(),
        ),
    ]


def test_stop_on_first_failure_and_safe_stage_output(capsys: pytest.CaptureFixture[str]) -> None:
    script = _load_script_module()
    calls: list[tuple[str, ...]] = []

    class _Completed:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    def _fake_run(command: list[str], *, cwd: Path, check: bool) -> _Completed:
        _ = cwd
        _ = check
        calls.append(tuple(command))
        if command[-1] == "tests/test_release_status_contract.py":
            return _Completed(1)
        return _Completed(0)

    exit_code = script.run_final_static_handoff_check(runner=_fake_run)
    captured = capsys.readouterr()

    assert exit_code == 1
    assert calls == [
        ("python", "-m", "pytest", "-q", "tests/test_project_handoff_contract.py"),
        ("python", "-m", "pytest", "-q", "tests/test_release_status_contract.py"),
    ]
    assert "mvp_final_static_handoff_check: fail" in captured.out
    assert "stage=release_status_contract" in captured.out


def test_health_check_runs_last() -> None:
    script = _load_script_module()
    commands = [command for _stage, command in script._STAGES]
    assert (
        "python",
        "-m",
        "pytest",
        "-q",
        "tests/test_mvp_release_staging_manifest_contract.py",
    ) in commands
    assert commands[-1] == ("python", "scripts/run_mvp_repo_release_health_check.py")


def test_only_lightweight_commands_used() -> None:
    script = _load_script_module()
    blob = " ".join(" ".join(command) for _stage, command in script._STAGES).lower()
    assert "run_mvp_release_preflight.py" not in blob
    assert "run_mvp_config_doctor.py" not in blob
    assert "run_postgres_mvp_smoke" not in blob
    assert "docker" not in blob


def test_output_has_no_forbidden_fragments_on_failure(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script_module()
    monkeypatch.setattr(script, "run_final_static_handoff_check", lambda: 1)

    exit_code = script.main()
    captured = capsys.readouterr()
    payload = (captured.out + captured.err).lower()

    assert exit_code == 1
    for fragment in _FORBIDDEN_FRAGMENTS:
        assert fragment.lower() not in payload
