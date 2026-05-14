"""Contract locks for full backend regression CI blocking gate."""

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


def test_full_backend_regression_blocking_step_contract_locked() -> None:
    text = _workflow_text()
    step = _step_block(text, "Run full backend regression tests (blocking gate)")
    assert "id: full_backend_regression" in step
    assert "continue-on-error:" not in step
    assert 'run: python -m pytest -q --junitxml="$REPORT_DIR/backend-full-regression.xml"' in step


def test_full_backend_regression_summary_marker_contract_locked() -> None:
    text = _workflow_text()
    step = _step_block(text, "Write full regression advisory summary marker")
    assert 'summary_path = report_dir / "backend-full-regression-summary.txt"' in step
    assert "full_regression_outcome=" in step
    assert 'outcome = "${{ steps.full_backend_regression.outcome }}"' in step
    assert 'normalized = outcome if outcome in {"success", "failure"} else "unknown"' in step
    assert '"success"' in step
    assert '"failure"' in step
    assert '"unknown"' in step


def test_validation_reports_artifact_path_still_covers_full_regression_marker() -> None:
    text = _workflow_text()
    upload_step = _step_block(text, "Upload validation reports artifact")
    assert "uses: actions/upload-artifact@v5" in upload_step
    assert "path: backend/test-reports" in upload_step
