"""Безопасный полный сброс явно помеченного тестового пользователя."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import asyncpg

from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderPort


class AdminTestUserResetError(RuntimeError):
    """Базовая ожидаемая ошибка операторского сброса."""


class AdminTestUserNotFoundError(AdminTestUserResetError):
    pass


class AdminTestUserRequiredError(AdminTestUserResetError):
    pass


class AdminTestUserConfirmationError(AdminTestUserResetError):
    pass


class AdminTestUserReasonError(AdminTestUserResetError):
    pass


class AdminTestUserVpnDeleteError(AdminTestUserResetError):
    pass


@dataclass(frozen=True, slots=True)
class AdminTestUserResetResult:
    telegram_user_id: int
    internal_user_id: str
    vpn_outcome: str
    deleted_rows: dict[str, int]
    correlation_id: str


def _affected_rows(command_tag: str) -> int:
    try:
        return int(command_tag.rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0


async def _delete(
    conn: asyncpg.Connection,
    deleted_rows: dict[str, int],
    name: str,
    query: str,
    *args: object,
) -> None:
    deleted_rows[name] = _affected_rows(await conn.execute(query, *args))


async def reset_test_user(
    *,
    pool: asyncpg.Pool,
    vless_provider: VlessProviderPort | None,
    internal_user_id: str,
    confirmation_telegram_user_id: int,
    reason: str,
    actor: str,
) -> AdminTestUserResetResult:
    """Отозвать VPN и атомарно удалить всё состояние internal test account.

    `internal_user_id` для Telegram детерминирован, поэтому сохранять любые продуктовые
    строки с этим ID нельзя: после следующего `/start` они снова станут состоянием того
    же пользователя.
    """

    clean_reason = reason.strip()
    if not 1 <= len(clean_reason) <= 300:
        raise AdminTestUserReasonError

    row = await pool.fetchrow(
        "SELECT telegram_user_id, is_internal FROM user_identities WHERE internal_user_id = $1",
        internal_user_id,
    )
    if row is None:
        raise AdminTestUserNotFoundError
    telegram_user_id = int(row["telegram_user_id"])
    if not bool(row["is_internal"]):
        raise AdminTestUserRequiredError
    if confirmation_telegram_user_id != telegram_user_id:
        raise AdminTestUserConfirmationError
    if vless_provider is None:
        raise AdminTestUserVpnDeleteError

    try:
        vpn_result = await vless_provider.delete_user(internal_user_id=internal_user_id)
    except Exception as exc:
        raise AdminTestUserVpnDeleteError from exc
    if vpn_result.outcome not in (VlessProviderOutcome.SUCCESS, VlessProviderOutcome.NOT_FOUND):
        raise AdminTestUserVpnDeleteError

    correlation_id = str(uuid.uuid4())
    deleted_rows: dict[str, int] = {}
    traffic_emails = (f"user-{internal_user_id[:16]}", f"x-user-{internal_user_id[:16]}")

    async with pool.acquire() as conn:
        async with conn.transaction():
            locked = await conn.fetchrow(
                "SELECT telegram_user_id, is_internal FROM user_identities WHERE internal_user_id = $1 FOR UPDATE",
                internal_user_id,
            )
            if locked is None:
                raise AdminTestUserNotFoundError
            if not bool(locked["is_internal"]):
                raise AdminTestUserRequiredError
            if int(locked["telegram_user_id"]) != telegram_user_id:
                raise AdminTestUserConfirmationError

            promo_counts = await conn.fetch(
                "SELECT code, count(*)::integer AS count FROM promo_redemptions "
                "WHERE internal_user_id = $1 GROUP BY code",
                internal_user_id,
            )
            for promo in promo_counts:
                await conn.execute(
                    "UPDATE promo_codes SET redemptions_count = GREATEST(0, redemptions_count - $2), "
                    "updated_at = NOW() WHERE code = $1",
                    promo["code"],
                    int(promo["count"]),
                )

            await _delete(
                conn,
                deleted_rows,
                "billing_ingestion_audit_events",
                "DELETE FROM billing_ingestion_audit_events WHERE internal_fact_ref IN "
                "(SELECT internal_fact_ref FROM billing_events_ledger WHERE internal_user_id = $1)",
                internal_user_id,
            )
            for name, query in (
                (
                    "billing_subscription_apply_audit_events",
                    "DELETE FROM billing_subscription_apply_audit_events WHERE internal_user_id = $1",
                ),
                (
                    "billing_subscription_apply_records",
                    "DELETE FROM billing_subscription_apply_records WHERE internal_user_id = $1",
                ),
                ("billing_events_ledger", "DELETE FROM billing_events_ledger WHERE internal_user_id = $1"),
                ("issuance_state", "DELETE FROM issuance_state WHERE internal_user_id = $1"),
                ("notification_log", "DELETE FROM notification_log WHERE internal_user_id = $1"),
                ("web_notifications", "DELETE FROM web_notifications WHERE internal_user_id = $1"),
                ("promo_applied", "DELETE FROM promo_applied WHERE internal_user_id = $1"),
                ("promo_redemptions", "DELETE FROM promo_redemptions WHERE internal_user_id = $1"),
                (
                    "admin_subscription_actions",
                    "DELETE FROM admin_subscription_actions WHERE internal_user_id = $1",
                ),
                ("funnel_interactions", "DELETE FROM funnel_interactions WHERE internal_user_id = $1"),
                ("experiment_assignments", "DELETE FROM experiment_assignments WHERE internal_user_id = $1"),
                (
                    "telegram_ads_attributions",
                    "DELETE FROM telegram_ads_attributions WHERE internal_user_id = $1",
                ),
            ):
                await _delete(conn, deleted_rows, name, query, internal_user_id)

            await _delete(
                conn,
                deleted_rows,
                "referral_bonus_awards",
                "DELETE FROM referral_bonus_awards WHERE referrer_user_id = $1",
                internal_user_id,
            )
            await _delete(
                conn,
                deleted_rows,
                "referral_transactions",
                "DELETE FROM referral_transactions WHERE internal_user_id = $1",
                internal_user_id,
            )
            await _delete(
                conn,
                deleted_rows,
                "referral_relationships",
                "DELETE FROM referral_relationships WHERE referred_user_id = $1",
                internal_user_id,
            )
            for name, query in (
                ("referral_balances", "DELETE FROM referral_balances WHERE internal_user_id = $1"),
                ("referral_codes", "DELETE FROM referral_codes WHERE internal_user_id = $1"),
            ):
                await _delete(conn, deleted_rows, name, query, internal_user_id)

            for name, query in (
                (
                    "broadcast_bonus_claims",
                    "DELETE FROM broadcast_bonus_claims WHERE telegram_user_id = $1",
                ),
                (
                    "broadcast_promo_claims",
                    "DELETE FROM broadcast_promo_claims WHERE telegram_user_id = $1",
                ),
            ):
                await _delete(conn, deleted_rows, name, query, telegram_user_id)

            for name, query in (
                ("bare_xray_traffic_state", "DELETE FROM bare_xray_traffic_state WHERE email = ANY($1::text[])"),
                ("bare_xray_traffic_daily", "DELETE FROM bare_xray_traffic_daily WHERE email = ANY($1::text[])"),
            ):
                await _delete(conn, deleted_rows, name, query, list(traffic_emails))

            await _delete(
                conn,
                deleted_rows,
                "subscription_snapshots",
                "DELETE FROM subscription_snapshots WHERE internal_user_id = $1",
                internal_user_id,
            )
            await _delete(
                conn,
                deleted_rows,
                "user_identities",
                "DELETE FROM user_identities WHERE internal_user_id = $1",
                internal_user_id,
            )
            if deleted_rows["user_identities"] != 1:
                raise AdminTestUserNotFoundError

            await conn.execute(
                "INSERT INTO admin_user_reset_actions "
                "(actor, telegram_user_id, internal_user_id, reason, vpn_outcome, deleted_rows, correlation_id) "
                "VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7)",
                actor,
                telegram_user_id,
                internal_user_id,
                clean_reason,
                str(vpn_result.outcome),
                json.dumps(deleted_rows, sort_keys=True),
                correlation_id,
            )

    return AdminTestUserResetResult(
        telegram_user_id=telegram_user_id,
        internal_user_id=internal_user_id,
        vpn_outcome=str(vpn_result.outcome),
        deleted_rows=deleted_rows,
        correlation_id=correlation_id,
    )
