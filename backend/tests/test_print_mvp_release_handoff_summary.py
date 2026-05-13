"""Unit tests for MVP release handoff summary script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "print_mvp_release_handoff_summary.py"
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
    spec = importlib.util.spec_from_file_location("print_mvp_release_handoff_summary", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_valid_fixture(base_dir: Path) -> None:
    (base_dir / "docs").mkdir(parents=True, exist_ok=True)
    (base_dir / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (base_dir / "docs" / "mvp_release_artifact_manifest.md").write_text("# manifest\n", encoding="utf-8")
    (base_dir / "docs" / "mvp_release_readiness_runbook.md").write_text("# runbook\n", encoding="utf-8")
    (base_dir / ".github" / "workflows" / "backend-mvp-release-readiness.yml").write_text(
        "name: backend-mvp-release-readiness\n",
        encoding="utf-8",
    )
    (base_dir / ".github" / "workflows" / "backend-postgres-mvp-smoke-validation.yml").write_text(
        "name: backend-postgres-mvp-smoke-validation\n",
        encoding="utf-8",
    )


def test_success_output_contains_required_markers(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)

    ok, issues, summary = script.generate_handoff_summary(backend_dir=tmp_path)

    assert ok is True
    assert issues == ()
    assert "mvp_release_handoff_summary" in summary
    assert "python scripts/run_mvp_release_readiness.py" in summary
    assert "python scripts/run_mvp_config_doctor.py --profile polling|webhook|internal-admin|retention|all" in summary
    assert "python scripts/run_postgres_mvp_smoke_local.py" in summary
    assert "backend-mvp-release-readiness" in summary
    assert "backend-postgres-mvp-smoke-validation" in summary
    assert "real operator config doctor profiles" in summary
    assert "local Docker smoke" in summary
    assert "deployed /healthz and /readyz" in summary
    assert "Telegram setWebhook and secret rotation" in summary
    assert "retention delete approval" in summary
    assert "public billing ingress" in summary
    assert "real provider SDK" in summary
    assert "raw credential/config delivery" in summary
    assert "full production SLO/alerting certification" in summary


def test_missing_manifest_or_runbook_returns_safe_issue_code(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    (tmp_path / "docs" / "mvp_release_artifact_manifest.md").unlink()

    ok, issues, _summary = script.generate_handoff_summary(backend_dir=tmp_path)

    assert ok is False
    assert "missing_release_handoff_source_doc" in issues


def test_script_does_not_run_subprocess_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)
    calls: list[str] = []

    def _should_not_run(*_args: object, **_kwargs: object) -> None:
        calls.append("called")
        raise AssertionError("subprocess should not be used")

    monkeypatch.setattr("subprocess.run", _should_not_run)

    ok, issues, _summary = script.generate_handoff_summary(backend_dir=tmp_path)
    assert ok is True
    assert issues == ()
    assert calls == []


def test_output_has_no_forbidden_fragments(tmp_path: Path) -> None:
    script = _load_script_module()
    _write_valid_fixture(tmp_path)

    ok, issues, summary = script.generate_handoff_summary(backend_dir=tmp_path)
    assert ok is True
    assert issues == ()

    blob = summary.lower()
    for forbidden in _FORBIDDEN_FRAGMENTS:
        assert forbidden.lower() not in blob
