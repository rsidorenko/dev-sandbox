"""Wiring helper: ADM-01 read ports + allowlist → :class:`Adm01LookupHandler` / internal Starlette (composition only)."""

from __future__ import annotations

from collections.abc import Sequence

from starlette.applications import Starlette

from app.admin_support.adm01_identity_resolve_adapter import Adm01IdentityResolveAdapter
from app.admin_support.adm01_internal_http import create_adm01_internal_http_app
from app.admin_support.adm01_lookup import Adm01LookupHandler
from app.admin_support.adm01_postgres_issuance_read_adapter import Adm01PostgresIssuanceReadAdapter
from app.admin_support.adm01_postgres_subscription_read_adapter import (
    Adm01PostgresSubscriptionReadAdapter,
)
from app.admin_support.adm01_subscription_entitlement_read_adapter import (
    Adm01SubscriptionEntitlementReadAdapter,
)
from app.admin_support.adm01_subscription_policy_read_adapter import (
    Adm01SubscriptionPolicyReadAdapter,
)
from app.admin_support.authorization import AllowlistAdm01Authorization
from app.admin_support.contracts import (
    Adm01EntitlementReadPort,
    Adm01IdentityResolvePort,
    Adm01IssuanceReadPort,
    Adm01PolicyReadPort,
    Adm01RedactionPort,
    Adm01SubscriptionReadPort,
)
from app.admin_support.principal_extraction import DefaultInternalAdminPrincipalExtractor
from app.persistence.postgres_issuance_state import PostgresIssuanceStateRepository
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository


def build_adm01_issuance_read_from_postgres_issuance_state(
    repository: PostgresIssuanceStateRepository,
) -> Adm01IssuanceReadPort:
    """:class:`Adm01PostgresIssuanceReadAdapter` is the supported issuance port for Postgres-persisted state."""
    return Adm01PostgresIssuanceReadAdapter(repository)


def build_adm01_subscription_read_from_postgres_snapshots(
    snapshots: PostgresSubscriptionSnapshotReader,
) -> Adm01SubscriptionReadPort:
    """:class:`Adm01PostgresSubscriptionReadAdapter` is the supported ADM-01 subscription read for Postgres."""
    return Adm01PostgresSubscriptionReadAdapter(snapshots)


def build_adm01_entitlement_read_from_postgres_snapshots(
    snapshots: PostgresSubscriptionSnapshotReader,
) -> Adm01EntitlementReadPort:
    """:class:`Adm01SubscriptionEntitlementReadAdapter` derives ADM-01 entitlement from snapshots."""
    return Adm01SubscriptionEntitlementReadAdapter(snapshots)


def build_adm01_policy_read_from_postgres_snapshots(
    snapshots: PostgresSubscriptionSnapshotReader,
) -> Adm01PolicyReadPort:
    """:class:`Adm01SubscriptionPolicyReadAdapter` derives ADM-01 policy from snapshots."""
    return Adm01SubscriptionPolicyReadAdapter(snapshots)


def build_adm01_identity_resolve_from_postgres_user_identities(
    identities: PostgresUserIdentityRepository,
) -> Adm01IdentityResolvePort:
    return Adm01IdentityResolveAdapter(identities)


def build_adm01_lookup_handler(
    *,
    identity: Adm01IdentityResolvePort,
    subscription: Adm01SubscriptionReadPort,
    entitlement: Adm01EntitlementReadPort,
    issuance: Adm01IssuanceReadPort,
    policy: Adm01PolicyReadPort,
    redaction: Adm01RedactionPort | None = None,
    adm01_allowlisted_internal_admin_principal_ids: Sequence[str],
) -> Adm01LookupHandler:
    return Adm01LookupHandler(
        authorization=AllowlistAdm01Authorization(adm01_allowlisted_internal_admin_principal_ids),
        identity=identity,
        subscription=subscription,
        entitlement=entitlement,
        issuance=issuance,
        policy=policy,
        redaction=redaction,
    )


def build_adm01_internal_lookup_http_app(
    *,
    identity: Adm01IdentityResolvePort,
    subscription: Adm01SubscriptionReadPort,
    entitlement: Adm01EntitlementReadPort,
    issuance: Adm01IssuanceReadPort,
    policy: Adm01PolicyReadPort,
    redaction: Adm01RedactionPort | None = None,
    adm01_allowlisted_internal_admin_principal_ids: Sequence[str],
) -> Starlette:
    handler = build_adm01_lookup_handler(
        identity=identity,
        subscription=subscription,
        entitlement=entitlement,
        issuance=issuance,
        policy=policy,
        redaction=redaction,
        adm01_allowlisted_internal_admin_principal_ids=adm01_allowlisted_internal_admin_principal_ids,
    )
    return create_adm01_internal_http_app(
        handler,
        DefaultInternalAdminPrincipalExtractor(),
    )
