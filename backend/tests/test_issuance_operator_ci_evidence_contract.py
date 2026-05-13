"""Contract locks for issuance operator smoke CI evidence artifacts/markers."""

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


def test_issuance_operator_smoke_blocking_step_contract_locked() -> None:
    text = _workflow_text()
    step = _step_block(text, "Run issuance operator entrypoint smoke (blocking gate)")
    assert "id: issuance_operator_entrypoint_smoke" in step
    assert "run: python scripts/check_issuance_operator_entrypoint_smoke.py" in step
    assert "continue-on-error: true" not in step


def test_issuance_operator_summary_marker_contract_locked() -> None:
    text = _workflow_text()
    step = _step_block(text, "Write issuance operator entrypoint smoke blocking summary marker")
    assert 'summary_path = report_dir / "backend-issuance-operator-entrypoint-smoke-summary.txt"' in step
    assert "issuance_operator_entrypoint_smoke_outcome=" in step
    assert 'outcome = "${{ steps.issuance_operator_entrypoint_smoke.outcome }}"' in step
    assert 'normalized = outcome if outcome in {"success", "failure"} else "unknown"' in step


def test_validation_reports_artifact_path_still_covers_summary_markers() -> None:
    text = _workflow_text()
    upload_step = _step_block(text, "Upload validation reports artifact")
    assert "uses: actions/upload-artifact@v5" in upload_step
    assert "path: backend/test-reports" in upload_step
