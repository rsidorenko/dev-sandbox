"""Opt-in slice-1 PostgreSQL composition helpers (pool lifecycle owned by caller / bundle)."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import asyncpg

from app.application.bootstrap import Slice1Composition, build_slice1_composition
from app.application.telegram_access_resend import IssuanceCurrentStateRef
from app.issuance.fake_provider import FakeIssuanceProvider, FakeProviderMode
from app.issuance.service import IssuanceService
from app.issuance.vless_issuance_adapter import VlessIssuanceAdapter
from app.issuance.vless_provider import StubVlessProvider
from app.issuance.xui_vless_provider import XuiVlessProvider
from app.persistence.issuance_state_record import IssuanceStatePersistence
from app.persistence.postgres_audit import PostgresAuditAppender
from app.persistence.postgres_idempotency import PostgresIdempotencyRepository
from app.persistence.postgres_issuance_state import PostgresIssuanceStateRepository
from app.persistence.postgres_outbound_delivery import PostgresOutboundDeliveryLedger
from app.persistence.postgres_referral import (
    PostgresReferralBalanceRepository,
    PostgresReferralCodeRepository,
    PostgresReferralRelationshipRepository,
    PostgresReferralTransactionRepository,
)
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_telegram_update_dedup import PostgresTelegramUpdateDedupGuard
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository
from app.security.config import ConfigurationError, RuntimeConfig

Slice1PostgresPoolOpener = Callable[[str], Awaitable[asyncpg.Pool]]


class _PostgresIssuanceStateLookup:
    def __init__(self, repo: PostgresIssuanceStateRepository) -> None:
        self._repo = repo

    async def get_current_for_user(self, internal_user_id: str) -> IssuanceCurrentStateRef | None:
        row = await self._repo.get_current_for_user(internal_user_id)
        if row is None:
            return None
        return IssuanceCurrentStateRef(
            issue_idempotency_key=row.issue_idempotency_key,
            is_revoked=(row.state is IssuanceStatePersistence.REVOKED),
        )


def slice1_postgres_repos_requested() -> bool:
    raw = os.environ.get("SLICE1_USE_POSTGRES_REPOS", "").strip().lower()
    return raw in ("1", "true", "yes")


async def _default_open_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=4)


async def resolve_slice1_composition_for_runtime(
    config: RuntimeConfig,
    *,
    open_pool: Slice1PostgresPoolOpener | None = None,
) -> tuple[Slice1Composition, asyncpg.Pool | None]:
    """
    Return slice-1 composition and optional asyncpg pool to close.

    When SLICE1_USE_POSTGRES_REPOS is unset/false, always in-memory (no pool).
    When enabled, requires a non-empty postgres config.database_url; pool open failures propagate.
    """
    if not slice1_postgres_repos_requested():
        return build_slice1_composition(
            bot_username=os.environ.get("BOT_USERNAME", ""),
            vless_provider=StubVlessProvider(),
        ), None

    dsn = (config.database_url or "").strip()
    if not dsn:
        raise ConfigurationError("missing or empty configuration: DATABASE_URL")

    opener = open_pool or _default_open_pool
    pool = await opener(dsn)

    # Use real VLESS provider if VPN servers exist in DB, otherwise stub
    try:
        servers = await pool.fetch("SELECT id FROM vpn_servers WHERE is_active = TRUE LIMIT 1")
        vless_provider = XuiVlessProvider(pool) if servers else StubVlessProvider()
    except Exception:
        vless_provider = StubVlessProvider()

    issuance_state_repo = PostgresIssuanceStateRepository(pool)
    composition = build_slice1_composition(
        issuance_service=IssuanceService(
            VlessIssuanceAdapter(vless_provider),
            operational_state=issuance_state_repo,
        ),
        issuance_state_lookup=_PostgresIssuanceStateLookup(issuance_state_repo),
        issuance_state_mutation=issuance_state_repo,
        identity=PostgresUserIdentityRepository(pool),
        idempotency=PostgresIdempotencyRepository(pool),
        snapshots=PostgresSubscriptionSnapshotReader(pool),
        audit=PostgresAuditAppender(pool),
        outbound_delivery=PostgresOutboundDeliveryLedger(pool),
        telegram_update_dedup=PostgresTelegramUpdateDedupGuard(pool),
        referral_code_repo=PostgresReferralCodeRepository(pool),
        referral_relationship_repo=PostgresReferralRelationshipRepository(pool),
        referral_balance_repo=PostgresReferralBalanceRepository(pool),
        referral_transaction_repo=PostgresReferralTransactionRepository(pool),
        bot_username=os.environ.get("BOT_USERNAME", ""),
        vless_provider=vless_provider,
    )
    return composition, pool
