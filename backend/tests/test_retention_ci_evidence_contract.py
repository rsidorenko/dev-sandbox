"""Contract locks for retention CI evidence artifacts/reports in workflow."""

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


def test_retention_integration_job_exists() -> None:
    text = _workflow_text()
    assert "  slice1-postgres-retention-integration:" in text


def test_retention_integration_junit_contract_locked() -> None:
    text = _workflow_text()
    assert '--junitxml="$REPORT_DIR/backend-postgres-retention-integration.xml"' in text

    verify_step = _step_block(text, "Verify retention integration JUnit report")
    assert 'test -f "$REPORT_DIR/backend-postgres-retention-integration.xml"' in verify_step
    assert 'test -s "$REPORT_DIR/backend-postgres-retention-integration.xml"' in verify_step


def test_retention_integration_artifact_contract_locked() -> None:
    text = _workflow_text()
    upload_step = _step_block(text, "Upload retention integration reports")
    assert "uses: actions/upload-artifact@v5" in upload_step
    assert "name: backend-postgres-retention-integration-reports" in upload_step
    assert "path: backend/test-reports" in upload_step
    assert "if-no-files-found: warn" in upload_step


def test_retention_integration_boundary_blocking_vs_advisory_locked() -> None:
    text = _workflow_text()

    retention_step = _step_block(text, "Run opt-in slice-1 retention integration tests (PostgreSQL service)")
    assert "continue-on-error:" not in retention_step

    composition_step = _step_block(text, "Run ADM-01 Postgres issuance composition check (advisory)")
    assert "continue-on-error: true" in composition_step

    operator_step = _step_block(text, "Run operator billing ingest/apply e2e smoke (advisory evidence)")
    assert "continue-on-error: true" in operator_step


def test_adm01_postgres_composition_advisory_evidence_contract_locked() -> None:
    text = _workflow_text()
    step = _step_block(text, "Run ADM-01 Postgres issuance composition check (advisory)")

    assert "id: adm01_postgres_issuance_composition" in step
    assert "continue-on-error: true" in step
    assert "run: python scripts/check_adm01_postgres_issuance_composition.py" in step

    assert "DATABASE_URL:" in step
    assert "postgresql://slice1ret_ci" in step
    assert "slice1retention_ci" in step
    assert 'ADM01_POSTGRES_ISSUANCE_COMPOSITION_CHECK_ENABLE: "1"' in step
