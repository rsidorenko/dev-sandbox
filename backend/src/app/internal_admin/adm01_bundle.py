"""ADM-01 internal lookup: typed dependency container → :mod:`app.admin_support.adm01_wiring` (delegate only)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from starlette.applications import Starlette

from app.admin_support.adm01_wiring import (
    build_adm01_internal_lookup_http_app,
    build_adm01_issuance_read_from_postgres_issuance_state,
)
from app.admin_support.contracts import (
    Adm01EntitlementReadPort,
    Adm01IdentityResolvePort,
    Adm01IssuanceReadPort,
    Adm01PolicyReadPort,
    Adm01RedactionPort,
    Adm01SubscriptionReadPort,
)
from app.persistence.postgres_issuance_state import PostgresIssuanceStateRepository


@dataclass(frozen=True, slots=True)
class Adm01InternalLookupDependencies:
    """Explicit ADM-01 read ports; issuance may be e.g. :class:`Adm01PostgresIssuanceReadAdapter`."""

    identity: Adm01IdentityResolvePort
    subscription: Adm01SubscriptionReadPort
    entitlement: Adm01EntitlementReadPort
    issuance: Adm01IssuanceReadPort
    policy: Adm01PolicyReadPort
    redaction: Adm01RedactionPort | None
    adm01_allowlisted_internal_admin_principal_ids: Sequence[str]


@dataclass(frozen=True, slots=True)
class Adm01InternalLookupWithPostgresIssuanceStateDependencies:
    """Same as :class:`Adm01InternalLookupDependencies` but issues reads via :class:`PostgresIssuanceStateRepository`."""

    identity: Adm01IdentityResolvePort
    subscription: Adm01SubscriptionReadPort
    entitlement: Adm01EntitlementReadPort
    postgres_issuance_state: PostgresIssuanceStateRepository
    policy: Adm01PolicyReadPort
    redaction: Adm01RedactionPort | None
    adm01_allowlisted_internal_admin_principal_ids: Sequence[str]


def build_adm01_internal_lookup_starlette_app(deps: Adm01InternalLookupDependencies) -> Starlette:
    return build_adm01_internal_lookup_http_app(
        identity=deps.identity,
        subscription=deps.subscription,
        entitlement=deps.entitlement,
        issuance=deps.issuance,
        policy=deps.policy,
        redaction=deps.redaction,
        adm01_allowlisted_internal_admin_principal_ids=deps.adm01_allowlisted_internal_admin_principal_ids,
    )


def build_adm01_internal_lookup_starlette_app_with_postgres_issuance_state(
    deps: Adm01InternalLookupWithPostgresIssuanceStateDependencies,
) -> Starlette:
    issuance = build_adm01_issuance_read_from_postgres_issuance_state(deps.postgres_issuance_state)
    return build_adm01_internal_lookup_starlette_app(
        Adm01InternalLookupDependencies(
            identity=deps.identity,
            subscription=deps.subscription,
            entitlement=deps.entitlement,
            issuance=issuance,
            policy=deps.policy,
            redaction=deps.redaction,
            adm01_allowlisted_internal_admin_principal_ids=deps.adm01_allowlisted_internal_admin_principal_ids,
        ),
    )
