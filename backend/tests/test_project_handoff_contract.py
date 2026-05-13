"""Contract checks for root project handoff index."""

from __future__ import annotations

from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent
_HANDOFF_PATH = _REPO_ROOT / "PROJECT_HANDOFF.md"

_REQUIRED_MARKERS = (
    "backend/RELEASE_STATUS.md",
    "cd backend && python scripts/run_mvp_release_readiness.py",
    "cd backend && python scripts/run_mvp_repo_release_health_check.py",
    "backend/tests/test_mvp_final_release_gate_contract.py",
    "tests/test_mvp_release_readiness_workflow_structure_contract.py",
    "backend-mvp-release-readiness",
    "PROJECT_HANDOFF.md",
    "backend release/handoff docs/scripts/tests",
    "cd backend && python scripts/run_mvp_final_static_handoff_check.py",
    "backend-postgres-mvp-smoke-validation",
    "backend/docs/mvp_release_artifact_manifest.md",
    "backend/docs/mvp_release_readiness_runbook.md",
    "backend/docs/postgres_mvp_smoke_runbook.md",
    "backend/docs/telegram_access_resend_runbook.md",
    "backend/docs/admin_support_internal_read_gate_runbook.md",
    "config doctor with real operator env",
    "local Docker smoke",
    "deployed webhook `/healthz` and `/readyz`",
    "Telegram `setWebhook` and secret rotation",
    "retention delete approval",
    "public billing ingress",
    "real provider SDK",
    "raw credential/config delivery",
    "full production SLO/alerting certification",
)

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


def _handoff_text() -> str:
    return _HANDOFF_PATH.read_text(encoding="utf-8")


def test_project_handoff_exists() -> None:
    assert _HANDOFF_PATH.exists()


def test_project_handoff_contains_required_markers() -> None:
    body = _handoff_text()
    for marker in _REQUIRED_MARKERS:
        assert marker in body


def test_project_handoff_has_no_forbidden_fragments() -> None:
    lowered = _handoff_text().lower()
    for fragment in _FORBIDDEN_FRAGMENTS:
        assert fragment.lower() not in lowered
