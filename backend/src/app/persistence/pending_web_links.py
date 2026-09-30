"""Временные ссылки: отдельные записи до оплаты, блокировки и безопасная очистка."""

from __future__ import annotations

import secrets

from app.web_api.legal import OFFER_VERSION


class LinkExpiredError(Exception):
    pass


async def create_link(pool, referral_code: str | None = None):
    return await pool.fetchrow(
        """INSERT INTO pending_web_links (internal_user_id, subscription_token, referral_code)
           VALUES ($1, $2, $3) RETURNING *""",
        f"web_{secrets.token_hex(16)}", secrets.token_urlsafe(32), referral_code,
    )


async def begin_attempt(pool, internal_user_id: str, provider: str, amount_kopecks: int):
    # Блокировка родителя общая с очисткой и подтверждением оплаты.
    async with pool.acquire() as conn:
        async with conn.transaction():
            link = await conn.fetchrow(
                "SELECT *, expires_at > clock_timestamp() AS live FROM pending_web_links WHERE internal_user_id=$1 FOR UPDATE",
                internal_user_id,
            )
            if not link or not link["live"] or link["promoted_at"] is not None:
                raise LinkExpiredError
            attempt_id = secrets.token_hex(16)
            await conn.execute(
                """INSERT INTO pending_web_payment_attempts
                   (attempt_id,internal_user_id,provider,amount_kopecks,offer_version)
                   VALUES ($1,$2,$3,$4,$5)""",
                attempt_id, internal_user_id, provider, amount_kopecks, OFFER_VERSION,
            )
            return attempt_id, link["expires_at"]


async def attach_payment(pool, attempt_id: str, payment_id: str):
    # Webhook может прийти раньше ответа create: succeeded нельзя вернуть в pending.
    await pool.execute(
        """UPDATE pending_web_payment_attempts SET provider_payment_id=$2,
           status=CASE WHEN status='creating' THEN 'pending' ELSE status END, updated_at=now()
           WHERE attempt_id=$1 AND (provider_payment_id IS NULL OR provider_payment_id=$2)""",
        attempt_id, payment_id,
    )


async def promote_paid_link(pool, *, internal_user_id: str, attempt_id: str,
                            provider: str, payment_id: str, amount_kopecks: int):
    """Вызывается только после проверки оплаты у провайдера; сохраняет исходный токен."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            link = await conn.fetchrow(
                "SELECT * FROM pending_web_links WHERE internal_user_id=$1 FOR UPDATE", internal_user_id,
            )
            if link is None:
                raise ValueError("pending_link_missing")
            attempt = await conn.fetchrow(
                "SELECT * FROM pending_web_payment_attempts WHERE attempt_id=$1 FOR UPDATE", attempt_id,
            )
            if (not attempt or attempt["internal_user_id"] != internal_user_id
                    or attempt["provider"] != provider or attempt["amount_kopecks"] != amount_kopecks
                    or attempt["provider_payment_id"] not in (None, payment_id)):
                raise ValueError("pending_payment_mismatch")
            await conn.execute(
                """INSERT INTO user_identities (telegram_user_id,internal_user_id,subscription_token)
                   VALUES ($1,$2,$3) ON CONFLICT (internal_user_id) DO NOTHING""",
                link["telegram_user_id"], internal_user_id, link["subscription_token"],
            )
            identity = await conn.fetchrow("SELECT telegram_user_id,subscription_token FROM user_identities WHERE internal_user_id=$1", internal_user_id)
            if identity["telegram_user_id"] != link["telegram_user_id"] or identity["subscription_token"] != link["subscription_token"]:
                raise ValueError("pending_identity_mismatch")
            await conn.execute(
                "UPDATE pending_web_links SET promoted_at=COALESCE(promoted_at,now()) WHERE internal_user_id=$1",
                internal_user_id,
            )
            await conn.execute(
                """UPDATE pending_web_payment_attempts SET status='succeeded',provider_payment_id=$2,
                   updated_at=now() WHERE attempt_id=$1""", attempt_id, payment_id,
            )
            # Реферальное отношение создаётся только после оплаты, в той же транзакции.
            if link["referral_code"]:
                referrer = await conn.fetchval("SELECT internal_user_id FROM referral_codes WHERE referral_code=$1", link["referral_code"])
                if referrer and referrer != internal_user_id:
                    parent = await conn.fetchval("SELECT referrer_user_id FROM referral_relationships WHERE referred_user_id=$1", referrer)
                    await conn.execute(
                        """INSERT INTO referral_relationships (relationship_id,referred_user_id,referrer_user_id,level,referrer_of_referrer_user_id)
                           VALUES ($1,$2,$3,1,$4) ON CONFLICT (referred_user_id) DO NOTHING""",
                        secrets.token_hex(16), internal_user_id, referrer, parent,
                    )


async def cleanup_expired_links(pool, *, delete: bool = False, limit: int = 100) -> int:
    """Только окончательно неоплаченные ссылки; unknown/creating запрещают удаление."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT internal_user_id FROM pending_web_links l
                   WHERE expires_at <= clock_timestamp() AND promoted_at IS NULL
                   AND NOT EXISTS (SELECT 1 FROM pending_web_payment_attempts p
                       WHERE p.internal_user_id=l.internal_user_id AND p.status <> 'canceled')
                   ORDER BY expires_at LIMIT $1 FOR UPDATE SKIP LOCKED""", max(1, min(limit, 1000)),
            )
            deleted = 0
            if delete:
                for row in rows:
                    # Повторная проверка после получения блокировки закрывает гонку со стартом оплаты.
                    removed = await conn.fetchval(
                        """DELETE FROM pending_web_links l WHERE internal_user_id=$1 AND promoted_at IS NULL
                           AND NOT EXISTS (SELECT 1 FROM pending_web_payment_attempts p
                               WHERE p.internal_user_id=l.internal_user_id AND p.status <> 'canceled') RETURNING internal_user_id""",
                        row["internal_user_id"],
                    )
                    deleted += removed is not None
            return deleted if delete else len(rows)
