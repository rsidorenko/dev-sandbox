"""Wiring helper: ADM-02 diagnostics ports + allowlist → internal Starlette app (composition only)."""

from __future__ import annotations

from collections.abc import Sequence

from starlette.applications import Starlette

from app.admin_support.adm02_diagnostics import Adm02DiagnosticsHandler
from app.admin_support.adm02_ensure_access import Adm02EnsureAccessHandler, NoopAdm02EnsureAccessAuditSink
from app.admin_support.adm02_ensure_access_audit_read import Adm02EnsureAccessAuditLookupHandler
from app.admin_support.adm02_ensure_access_mutation import FixedAdm02MutationOptIn
from app.admin_support.adm02_internal_http import create_adm02_internal_http_app
from app.admin_support.authorization import AllowlistAdm02Authorization
from app.admin_support.contracts import (
    Adm01IdentityResolvePort,
    Adm01IssuanceReadPort,
    Adm01SubscriptionReadPort,
    Adm02BillingFactsReadPort,
    Adm02EnsureAccessAuditPort,
    Adm02EnsureAccessAuditReadPort,
    Adm02EnsureAccessMutationPort,
    Adm02FactOfAccessAuditPort,
    Adm02QuarantineReadPort,
    Adm02ReconciliationReadPort,
    Adm02RedactionPort,
)
from app.admin_support.principal_extraction import DefaultInternalAdminPrincipalExtractor


def build_adm02_internal_diagnostics_http_app(
    *,
    identity: Adm01IdentityResolvePort,
    billing: Adm02BillingFactsReadPort,
    quarantine: Adm02QuarantineReadPort,
    reconciliation: Adm02ReconciliationReadPort,
    audit: Adm02FactOfAccessAuditPort,
    redaction: Adm02RedactionPort | None = None,
    adm02_allowlisted_internal_admin_principal_ids: Sequence[str],
) -> Starlette:
    handler = Adm02DiagnosticsHandler(
        authorization=AllowlistAdm02Authorization(adm02_allowlisted_internal_admin_principal_ids),
        identity=identity,
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        audit=audit,
        redaction=redaction,
    )
    return create_adm02_internal_http_app(
        handler,
        DefaultInternalAdminPrincipalExtractor(),
    )


def build_adm02_ensure_access_handler(
    *,
    identity: Adm01IdentityResolvePort,
    subscription: Adm01SubscriptionReadPort,
    issuance: Adm01IssuanceReadPort,
    mutation: Adm02EnsureAccessMutationPort,
    audit: Adm02EnsureAccessAuditPort | None = None,
    adm02_allowlisted_internal_admin_principal_ids: Sequence[str],
    adm02_mutation_opt_in_enabled: bool,
) -> Adm02EnsureAccessHandler:
    return Adm02EnsureAccessHandler(
        authorization=AllowlistAdm02Authorization(adm02_allowlisted_internal_admin_principal_ids),
        mutation_opt_in=FixedAdm02MutationOptIn(adm02_mutation_opt_in_enabled),
        identity=identity,
        subscription=subscription,
        issuance=issuance,
        mutation=mutation,
        audit=audit or NoopAdm02EnsureAccessAuditSink(),
    )


def build_adm02_ensure_access_audit_lookup_handler(
    *,
    audit_read: Adm02EnsureAccessAuditReadPort,
    adm02_allowlisted_internal_admin_principal_ids: Sequence[str],
) -> Adm02EnsureAccessAuditLookupHandler:
    return Adm02EnsureAccessAuditLookupHandler(
        authorization=AllowlistAdm02Authorization(adm02_allowlisted_internal_admin_principal_ids),
        audit_read=audit_read,
    )


def build_adm02_internal_support_http_app(
    *,
    identity: Adm01IdentityResolvePort,
    billing: Adm02BillingFactsReadPort,
    quarantine: Adm02QuarantineReadPort,
    reconciliation: Adm02ReconciliationReadPort,
    audit: Adm02FactOfAccessAuditPort,
    subscription: Adm01SubscriptionReadPort,
    issuance: Adm01IssuanceReadPort,
    ensure_access_mutation: Adm02EnsureAccessMutationPort,
    ensure_access_audit: Adm02EnsureAccessAuditPort | None = None,
    ensure_access_audit_read: Adm02EnsureAccessAuditReadPort | None = None,
    redaction: Adm02RedactionPort | None = None,
    adm02_allowlisted_internal_admin_principal_ids: Sequence[str],
    adm02_mutation_opt_in_enabled: bool,
) -> Starlette:
    diagnostics_handler = Adm02DiagnosticsHandler(
        authorization=AllowlistAdm02Authorization(adm02_allowlisted_internal_admin_principal_ids),
        identity=identity,
        billing=billing,
        quarantine=quarantine,
        reconciliation=reconciliation,
        audit=audit,
        redaction=redaction,
    )
    ensure_access_handler = None
    ensure_access_audit_lookup_handler = None
    if adm02_mutation_opt_in_enabled:
        ensure_access_handler = build_adm02_ensure_access_handler(
            identity=identity,
            subscription=subscription,
            issuance=issuance,
            mutation=ensure_access_mutation,
            audit=ensure_access_audit,
            adm02_allowlisted_internal_admin_principal_ids=adm02_allowlisted_internal_admin_principal_ids,
            adm02_mutation_opt_in_enabled=adm02_mutation_opt_in_enabled,
        )
    if ensure_access_audit_read is not None:
        ensure_access_audit_lookup_handler = build_adm02_ensure_access_audit_lookup_handler(
            audit_read=ensure_access_audit_read,
            adm02_allowlisted_internal_admin_principal_ids=adm02_allowlisted_internal_admin_principal_ids,
        )
    return create_adm02_internal_http_app(
        diagnostics_handler,
        DefaultInternalAdminPrincipalExtractor(),
        ensure_access_handler=ensure_access_handler,
        ensure_access_audit_lookup_handler=ensure_access_audit_lookup_handler,
    )
