"""Contract locks for admin support internal read gate CI advisory evidence."""

from __future__ import annotations

from pathlib import Path

_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "backend-postgres-mvp-smoke-validation.yml"
)


def _workflow_text() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def _step_block(text: str, step_name: str) -> str:
    marker = f"      - name: {step_name}"
    start = text.find(marker)
    assert start != -1, f"missing workflow step: {step_name}"
    next_step = text.find("\n      - name:", start + len(marker))
    if next_step == -1:
        return text[start:]
    return text[start:next_step]


def test_admin_support_internal_read_gate_advisory_step_contract_locked() -> None:
    text = _workflow_text()
    step = _step_block(text, "Run admin support internal read gate (advisory evidence)")
    assert "id: admin_support_internal_read_gate" in step
    assert "continue-on-error: true" in step
    assert "run: python scripts/check_admin_support_internal_read_gate.py" in step


def test_admin_support_internal_read_gate_summary_marker_contract_locked() -> None:
    text = _workflow_text()
    step = _step_block(text, "Write admin support internal read gate advisory summary marker")
    assert 'summary_path = report_dir / "backend-admin-support-internal-read-gate-summary.txt"' in step
    assert "internal_read_gate_outcome=" in step
    assert 'outcome = "${{ steps.admin_support_internal_read_gate.outcome }}"' in step
    assert 'normalized = outcome if outcome in {"success", "failure"} else "unknown"' in step
    assert '"success"' in step
    assert '"failure"' in step
    assert '"unknown"' in step


def test_validation_reports_artifact_path_still_covers_admin_support_marker() -> None:
    text = _workflow_text()
    upload_step = _step_block(text, "Upload validation reports artifact")
    assert "uses: actions/upload-artifact@v5" in upload_step
    assert "path: backend/test-reports" in upload_step
