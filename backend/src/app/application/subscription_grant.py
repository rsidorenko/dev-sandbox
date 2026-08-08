"""System/operator grant of subscription days.

Shared by:
  * operator add-days (``web_api/admin_users.py``) — ``actor`` = admin identity;
  * referral 'free month per friend' bonus (``runtime/fulfillment_processor.py``) — ``actor = "referral_bonus"``.

The grant extends ``subscription_snapshots.active_until_utc`` (or creates a ``custom:{days}``
subscription from scratch), re-activates the snapshot, re-provisions VLESS keys across servers,
and writes an audit row to ``admin_subscription_actions``. It is deliberately NOT recorded in
``billing_events_ledger`` — a free grant must not distort revenue/reconciliation. See ADRs in
``docs/architecture/`` and the admin add-days docstring.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from app.domain.plans import DEFAULT_DEVICE_LIMIT
from app.issuance.vless_provider import VlessProviderOutcome

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GrantResult:
    """Outcome of a days grant. `created` is True when the user had no snapshot and a
    `custom:{days}` subscription was created from scratch."""

    created: bool
    days: int
    old_active_until: datetime | None
    new_active_until: datetime
    plan_id: str
    device_count: int
    provisioning: str
    servers_ok: int
    correlation_id: str


async def grant_subscription_days(
    *,
    pool: asyncpg.Pool,
    vless_provider: Any | None,
    internal_user_id: str,
    days: int,
    actor: str,
    correlation_id: str | None = None,
) -> GrantResult:
    """Grant ``days`` of subscription to ``internal_user_id``: extend an active subscription
    (from its current end) or create one from scratch, reactivate, re-provision VLESS, and audit.

    extend mode (snapshot exists): prolongs ``active_until_utc`` (from the current end, or from
    ``now`` if expired/cancelled), reactivates (``state='active'``, ``keys_*_at=NULL``).
    INVARIANT: ``plan_id`` and ``device_count`` of an existing subscription are NOT changed.
    create mode (no snapshot): ``plan_id = 'custom:{days}'``, ``device_count = DEFAULT_DEVICE_LIMIT``,
    ``active_until = now + days`` — analogous to the /start trial but a paid-length grant.

    Idempotent at the snapshot level (UPSERT). Once-only semantics are the caller's responsibility:
    operator action (admin) or ``referral_bonus_awards`` UNIQUE(referrer, payer) (bonus).
    """
    correlation_id = correlation_id or f"grant-{uuid.uuid4().hex[:12]}"

    snap = await pool.fetchrow(
        "SELECT state_label, plan_id, device_count, active_until_utc, "
        "keys_deactivated_at, keys_deleted_at FROM subscription_snapshots "
        "WHERE internal_user_id = $1",
        internal_user_id,
    )

    now = datetime.now(UTC)
    created = snap is None
    if created:
        base = None
        plan_id_for_write = f"custom:{days}"
        device_count_for_write = DEFAULT_DEVICE_LIMIT
    else:
        base = snap["active_until_utc"]
        plan_id_for_write = snap["plan_id"]
        device_count_for_write = snap["device_count"] or DEFAULT_DEVICE_LIMIT

    # Активную — продлеваем от текущего срока; истёкшую/отменённую/новую — от now.
    start = base if (base is not None and base > now) else now
    new_until = start + timedelta(days=days)

    await pool.execute(
        """INSERT INTO subscription_snapshots
               (internal_user_id, state_label, active_until_utc, plan_id, device_count)
           VALUES ($1, 'active', $2, $3, $4)
           ON CONFLICT (internal_user_id) DO UPDATE
           SET state_label = 'active',
               active_until_utc = EXCLUDED.active_until_utc,
               keys_deactivated_at = NULL,
               keys_deleted_at = NULL,
               plan_id = subscription_snapshots.plan_id,
               device_count = subscription_snapshots.device_count,
               updated_at = NOW()""",
        internal_user_id,
        new_until,
        plan_id_for_write,
        device_count_for_write,
    )

    # Перепровижининг VLESS-клиента на всех серверах с новым сроком. create_user если ключей
    # нет/удалены/активны, activate_user если деактивированы. Best-effort: сбой панели не
    # откатывает изменение в БД — логируем и считаем результат.
    expiry_days = max(1, math.ceil((new_until - now).total_seconds() / 86_400))
    provisioning = "skipped"
    servers_ok = 0
    if vless_provider is None:
        provisioning = "skipped_no_provider"
    else:
        try:
            if created or snap["keys_deleted_at"] is not None or snap["keys_deactivated_at"] is None:
                pres = await vless_provider.create_user(
                    internal_user_id=internal_user_id,
                    device_count=device_count_for_write,
                    expiry_days=expiry_days,
                )
            else:
                pres = await vless_provider.activate_user(
                    internal_user_id=internal_user_id,
                    device_count=device_count_for_write,
                    expiry_days=expiry_days,
                )
            if pres.outcome == VlessProviderOutcome.SUCCESS:
                servers_ok = len(pres.config.servers) if pres.config is not None else 0
                provisioning = f"success servers={servers_ok}"
            else:
                provisioning = f"outcome={pres.outcome}"
        except Exception:
            _LOGGER.exception(
                "grant_subscription_days provisioning failed user=%s actor=%s",
                internal_user_id[:8],
                actor,
            )
            provisioning = "failed"

    # Аудит бесплатного гранта (отдельно от billing — не искажает выручку).
    try:
        await pool.execute(
            """INSERT INTO admin_subscription_actions
               (actor, internal_user_id, action, days, old_active_until, new_active_until,
                plan_id, device_count, provisioning, correlation_id)
               VALUES ($1,$2,'add_days',$3,$4,$5,$6,$7,$8,$9)""",
            actor,
            internal_user_id,
            days,
            base,
            new_until,
            plan_id_for_write,
            device_count_for_write,
            provisioning,
            correlation_id,
        )
    except Exception:
        _LOGGER.exception(
            "grant_subscription_days audit insert failed user=%s actor=%s",
            internal_user_id[:8],
            actor,
        )

    return GrantResult(
        created=created,
        days=days,
        old_active_until=base,
        new_active_until=new_until,
        plan_id=plan_id_for_write,
        device_count=device_count_for_write,
        provisioning=provisioning,
        servers_ok=servers_ok,
        correlation_id=correlation_id,
    )
