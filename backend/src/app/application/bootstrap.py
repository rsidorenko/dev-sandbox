"""Thin slice-1 composition: in-memory adapters + UC-01 / UC-02 handlers (no framework, no transport)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from app.application.handlers import BootstrapIdentityHandler, GetSubscriptionStatusHandler
from app.application.interfaces import (
    AuditAppender,
    IdempotencyRepository,
    OutboundDeliveryLedger,
    SubscriptionSnapshot,
    SubscriptionSnapshotReader,
    SubscriptionSnapshotWriter,
    UserIdentityRepository,
)
from app.application.telegram_access_resend import (
    AccessResendCooldownStore,
    InMemoryAccessResendCooldownStore,
    IssuanceStateForResendLookup,
    IssuanceStateForResendMutation,
    TelegramAccessResendDisabledHitMarker,
    TelegramAccessResendHandler,
    telegram_access_resend_enabled_from_env,
)
from app.application.telegram_command_rate_limit import (
    InMemoryTelegramCommandRateLimiter,
    TelegramCommandRateLimiter,
)
from app.application.telegram_command_rate_limit_telemetry import (
    StructuredLoggingTelegramCommandRateLimitTelemetry,
    TelegramCommandRateLimitTelemetry,
)
from app.application.telegram_update_dedup import (
    InMemoryTelegramUpdateDedupGuard,
    TelegramUpdateDedupGuard,
)
from app.issuance.service import IssuanceService
from app.issuance.vless_provider import VlessProviderPort
from app.persistence.in_memory import (
    InMemoryAuditAppender,
    InMemoryIdempotencyRepository,
    InMemoryOutboundDeliveryLedger,
    InMemoryReferralBalanceRepository,
    InMemoryReferralCodeRepository,
    InMemoryReferralRelationshipRepository,
    InMemoryReferralTransactionRepository,
    InMemorySubscriptionSnapshotReader,
    InMemoryUserIdentityRepository,
)
from app.persistence.referral_contracts import (
    ReferralBalanceRepository,
    ReferralCodeRepository,
    ReferralRelationshipRepository,
    ReferralTransactionRepository,
)


@dataclass(frozen=True, slots=True)
class Slice1Composition:
    """Wired handlers for slice 1; shared identity store links UC-01 and UC-02."""

    bootstrap: BootstrapIdentityHandler
    get_status: GetSubscriptionStatusHandler
    identity: UserIdentityRepository
    idempotency: IdempotencyRepository
    audit: AuditAppender
    snapshots: SubscriptionSnapshotReader
    outbound_delivery: OutboundDeliveryLedger
    access_resend: TelegramAccessResendHandler
    command_rate_limiter: TelegramCommandRateLimiter
    command_rate_limit_telemetry: TelegramCommandRateLimitTelemetry
    telegram_update_dedup: TelegramUpdateDedupGuard
    referral_code_repo: ReferralCodeRepository
    referral_relationship_repo: ReferralRelationshipRepository
    referral_balance_repo: ReferralBalanceRepository
    referral_transaction_repo: ReferralTransactionRepository
    bot_username: str
    vless_provider: VlessProviderPort | None = None


def build_slice1_composition(
    *,
    initial_snapshots: Mapping[str, SubscriptionSnapshot] | None = None,
    identity: UserIdentityRepository | None = None,
    idempotency: IdempotencyRepository | None = None,
    snapshots: SubscriptionSnapshotReader | None = None,
    audit: AuditAppender | None = None,
    outbound_delivery: OutboundDeliveryLedger | None = None,
    issuance_service: IssuanceService | None = None,
    issuance_state_lookup: IssuanceStateForResendLookup | None = None,
    issuance_state_mutation: IssuanceStateForResendMutation | None = None,
    resend_cooldown: AccessResendCooldownStore | None = None,
    resend_disabled_hit_marker: TelegramAccessResendDisabledHitMarker | None = None,
    access_resend_enabled: bool | None = None,
    command_rate_limiter: TelegramCommandRateLimiter | None = None,
    command_rate_limit_telemetry: TelegramCommandRateLimitTelemetry | None = None,
    telegram_update_dedup: TelegramUpdateDedupGuard | None = None,
    referral_code_repo: ReferralCodeRepository | None = None,
    referral_relationship_repo: ReferralRelationshipRepository | None = None,
    referral_balance_repo: ReferralBalanceRepository | None = None,
    referral_transaction_repo: ReferralTransactionRepository | None = None,
    bot_username: str | None = None,
    vless_provider: VlessProviderPort | None = None,
) -> Slice1Composition:
    if (identity is None) ^ (idempotency is None):
        raise ValueError("identity and idempotency must both be provided or both omitted")
    if identity is None:
        if snapshots is not None:
            raise ValueError("snapshots must be omitted when identity and idempotency are defaulted")
        if audit is not None:
            raise ValueError("audit must be omitted when identity and idempotency are defaulted")
        identity = InMemoryUserIdentityRepository()
        idempotency = InMemoryIdempotencyRepository()
    elif snapshots is None:
        raise ValueError("snapshots must be provided when identity and idempotency are explicit")
    if audit is None:
        audit = InMemoryAuditAppender()
    if snapshots is None:
        snapshots = InMemorySubscriptionSnapshotReader(initial_snapshots)
    snapshot_writer = cast("SubscriptionSnapshotWriter", snapshots)
    delivery = outbound_delivery or InMemoryOutboundDeliveryLedger()
    cooldown = resend_cooldown or InMemoryAccessResendCooldownStore()
    dedup = telegram_update_dedup or InMemoryTelegramUpdateDedupGuard()
    rate_limiter = command_rate_limiter or InMemoryTelegramCommandRateLimiter()
    rate_telemetry = command_rate_limit_telemetry or StructuredLoggingTelegramCommandRateLimitTelemetry()
    enabled = (
        access_resend_enabled
        if access_resend_enabled is not None
        else telegram_access_resend_enabled_from_env(os.environ.get)
    )
    ref_code = referral_code_repo or InMemoryReferralCodeRepository()
    ref_rel = referral_relationship_repo or InMemoryReferralRelationshipRepository()
    ref_bal = referral_balance_repo or InMemoryReferralBalanceRepository()
    ref_tx = referral_transaction_repo or InMemoryReferralTransactionRepository()
    resolved_bot_username = bot_username or os.environ.get("BOT_USERNAME", "")
    return Slice1Composition(
        bootstrap=BootstrapIdentityHandler(identity, idempotency, audit, snapshot_writer),
        get_status=GetSubscriptionStatusHandler(
            identity,
            snapshots,
            issuance_state_lookup=issuance_state_lookup,
        ),
        identity=identity,
        idempotency=idempotency,
        audit=audit,
        snapshots=snapshots,
        outbound_delivery=delivery,
        access_resend=TelegramAccessResendHandler(
            identity=identity,
            snapshots=snapshots,
            issuance_service=issuance_service,
            issuance_state_lookup=issuance_state_lookup,
            issuance_state_mutation=issuance_state_mutation,
            cooldown=cooldown,
            disabled_hit_marker=resend_disabled_hit_marker,
            enabled=enabled,
        ),
        command_rate_limiter=rate_limiter,
        command_rate_limit_telemetry=rate_telemetry,
        telegram_update_dedup=dedup,
        referral_code_repo=ref_code,
        referral_relationship_repo=ref_rel,
        referral_balance_repo=ref_bal,
        referral_transaction_repo=ref_tx,
        bot_username=resolved_bot_username,
        vless_provider=vless_provider,
    )
