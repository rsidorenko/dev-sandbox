"""In-process advisory gate checks for ADM-01/ADM-02 internal HTTP surfaces (ASGI memory only).

Safe-by-default: no env reads, no outbound network, no logging of request bodies or secrets.
ADM-02 success exercises handler semantics including in-memory fact-of-access append (see runbook).
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.admin_support.adm01_internal_http import ADM01_INTERNAL_LOOKUP_PATH, create_adm01_internal_http_app
from app.admin_support.adm01_lookup import Adm01LookupHandler
from app.admin_support.adm02_billing_facts_ledger_adapter import Adm02BillingFactsLedgerReadAdapter
from app.admin_support.adm02_fact_of_access_audit_adapter import Adm02FactOfAccessPersistenceAuditAdapter
from app.admin_support.adm02_internal_http import ADM02_INTERNAL_DIAGNOSTICS_PATH
from app.admin_support.adm02_quarantine_mismatch_adapter import Adm02QuarantineMismatchReadAdapter
from app.admin_support.adm02_reconciliation_runs_adapter import Adm02ReconciliationRunsReadAdapter
from app.admin_support.adm02_wiring import build_adm02_internal_diagnostics_http_app
from app.admin_support.authorization import AllowlistAdm01Authorization
from app.admin_support.contracts import (
    AdminPolicyFlag,
    EntitlementSummary,
    EntitlementSummaryCategory,
    InternalUserTarget,
    IssuanceOperationalState,
    IssuanceOperationalSummary,
)
from app.admin_support.principal_extraction import DefaultInternalAdminPrincipalExtractor
from app.application.interfaces import SubscriptionSnapshot
from app.persistence.adm02_fact_of_access import InMemoryAdm02FactOfAccessRecordAppender
from app.persistence.billing_events_ledger_in_memory import InMemoryBillingEventsLedgerRepository
from app.persistence.mismatch_quarantine_in_memory import InMemoryMismatchQuarantineRepository
from app.persistence.reconciliation_runs_in_memory import InMemoryReconciliationRunsRepository
from app.shared.correlation import new_correlation_id

# Stable synthetic allowlist entries for gate checks only (not production identifiers).
_GATE_ADM01_PRINCIPAL = "slice1-internal-read-gate-adm01"
_GATE_ADM02_PRINCIPAL = "slice1-internal-read-gate-adm02"


class _IdentityEchoInternalUserId:
    async def resolve_internal_user_id(self, target, *, correlation_id: str) -> str | None:
        if isinstance(target, InternalUserTarget):
            return target.internal_user_id
        return None


class _SubscriptionReadMinimal:
    async def get_subscription_snapshot(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        return SubscriptionSnapshot(internal_user_id=internal_user_id, state_label="inactive")


class _EntitlementReadMinimal:
    async def get_entitlement_summary(self, internal_user_id: str) -> EntitlementSummary:
        return EntitlementSummary(category=EntitlementSummaryCategory.UNKNOWN)


class _IssuanceReadMinimal:
    async def get_issuance_summary(self, internal_user_id: str) -> IssuanceOperationalSummary:
        return IssuanceOperationalSummary(state=IssuanceOperationalState.UNKNOWN)


class _PolicyReadMinimal:
    async def get_policy_flag(self, internal_user_id: str) -> AdminPolicyFlag:
        return AdminPolicyFlag.DEFAULT


def _build_adm01_gate_app():
    handler = Adm01LookupHandler(
        authorization=AllowlistAdm01Authorization([_GATE_ADM01_PRINCIPAL]),
        identity=_IdentityEchoInternalUserId(),
        subscription=_SubscriptionReadMinimal(),
        entitlement=_EntitlementReadMinimal(),
        issuance=_IssuanceReadMinimal(),
        policy=_PolicyReadMinimal(),
        redaction=None,
    )
    return create_adm01_internal_http_app(handler, DefaultInternalAdminPrincipalExtractor())


def _build_adm02_gate_app():
    ledger = InMemoryBillingEventsLedgerRepository()
    billing = Adm02BillingFactsLedgerReadAdapter(ledger)
    quarantine_repo = InMemoryMismatchQuarantineRepository()
    quarantine = Adm02QuarantineMismatchReadAdapter(quarantine_repo)
    recon_repo = InMemoryReconciliationRunsRepository()
    reconciliation = Adm02ReconciliationRunsReadAdapter(recon_repo)
    fixed_now = datetime(2026, 4, 24, 0, 0, 0, tzinfo=UTC)
    persisted = InMemoryAdm02FactOfAccessRecordAppender()
    audit = Adm02FactOfAccessPersistenceAuditAdapter(
        appender=persisted,
        now_provider=lambda: fixed_now,
    )
    return build_adm02_internal_diagnostics_http_app(
        identity=_IdentityEchoInternalUserId(),
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        audit=audit,
        redaction=None,
        adm02_allowlisted_internal_admin_principal_ids=[_GATE_ADM02_PRINCIPAL],
    )


async def _post_json(app: object, path: str, payload: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://gate.test") as client:
        return await client.post(path, json=payload)


async def _post_raw(app: object, path: str, content: bytes) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://gate.test") as client:
        return await client.post(
            path,
            content=content,
            headers={"Content-Type": "application/json"},
        )


def _denied_response_has_no_summary_leak(body: dict) -> bool:
    if body.get("outcome") != "denied":
        return False
    if body.get("summary") is not None:
        return False
    allowed = {"outcome", "correlation_id", "summary"}
    return set(body.keys()) <= allowed


async def run_admin_support_internal_read_gate_checks() -> None:
    """Run ADM-01/ADM-02 internal read gate scenarios; raise RuntimeError on failure (generic messages only)."""
    cid_adm01 = new_correlation_id()
    cid_adm02 = new_correlation_id()
    adm01 = _build_adm01_gate_app()
    adm02 = _build_adm02_gate_app()

    r_deny = await _post_json(
        adm01,
        ADM01_INTERNAL_LOOKUP_PATH,
        {
            "correlation_id": cid_adm01,
            "internal_admin_principal_id": "not-on-allowlist",
            "internal_user_id": "u-gate",
        },
    )
    if r_deny.status_code != 200:
        raise RuntimeError("adm01 denied scenario unexpected status")
    body_deny = r_deny.json()
    if not _denied_response_has_no_summary_leak(body_deny):
        raise RuntimeError("adm01 denied scenario response shape")

    marker_internal_user = "u-internal-read-gate-1"
    r_allow = await _post_json(
        adm01,
        ADM01_INTERNAL_LOOKUP_PATH,
        {
            "correlation_id": cid_adm01,
            "internal_admin_principal_id": f"  {_GATE_ADM01_PRINCIPAL}  ",
            "internal_user_id": marker_internal_user,
        },
    )
    if r_allow.status_code != 200:
        raise RuntimeError("adm01 allow scenario unexpected status")
    body_ok = r_allow.json()
    if body_ok.get("outcome") != "success":
        raise RuntimeError("adm01 allow scenario outcome")
    summary = body_ok.get("summary")
    if not isinstance(summary, dict):
        raise RuntimeError("adm01 allow scenario summary")
    if summary.get("telegram_identity_known") is not True:
        raise RuntimeError("adm01 allow scenario identity marker")
    if summary.get("access_readiness_bucket") not in {
        "active_access_not_ready",
        "not_applicable_no_active_subscription",
    }:
        raise RuntimeError("adm01 allow scenario readiness marker")
    raw = r_allow.text
    if "postgresql://" in raw.lower():
        raise RuntimeError("adm01 allow scenario response url marker")

    r_bad = await _post_raw(adm01, ADM01_INTERNAL_LOOKUP_PATH, b"{not json")
    if r_bad.status_code != 400:
        raise RuntimeError("adm01 invalid json status")
    err_body = r_bad.json()
    if set(err_body.keys()) != {"error"}:
        raise RuntimeError("adm01 invalid json body shape")

    r2_bad = await _post_raw(adm02, ADM02_INTERNAL_DIAGNOSTICS_PATH, b"{not json")
    if r2_bad.status_code != 400:
        raise RuntimeError("adm02 invalid json status")
    err2 = r2_bad.json()
    if set(err2.keys()) != {"error"}:
        raise RuntimeError("adm02 invalid json body shape")

    r2_deny = await _post_json(
        adm02,
        ADM02_INTERNAL_DIAGNOSTICS_PATH,
        {
            "correlation_id": cid_adm02,
            "internal_admin_principal_id": "intruder-adm02",
            "internal_user_id": "u-gate",
        },
    )
    if r2_deny.status_code != 200 or not _denied_response_has_no_summary_leak(r2_deny.json()):
        raise RuntimeError("adm02 denied scenario")

    r2_ok = await _post_json(
        adm02,
        ADM02_INTERNAL_DIAGNOSTICS_PATH,
        {
            "correlation_id": cid_adm02,
            "internal_admin_principal_id": _GATE_ADM02_PRINCIPAL,
            "internal_user_id": "u-gate",
        },
    )
    if r2_ok.status_code != 200:
        raise RuntimeError("adm02 allow scenario status")
    b2 = r2_ok.json()
    if b2.get("outcome") != "success":
        raise RuntimeError("adm02 allow scenario outcome")
    s2 = b2.get("summary")
    if not isinstance(s2, dict):
        raise RuntimeError("adm02 allow scenario summary")
    for key in (
        "billing_category",
        "internal_fact_refs",
        "quarantine_marker",
        "quarantine_reason_code",
        "reconciliation_last_run_marker",
        "redaction",
    ):
        if key not in s2:
            raise RuntimeError("adm02 allow scenario summary keys")
