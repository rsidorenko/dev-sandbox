"""Сверка незавершённых платежей и очистка временных ссылок; отдельный процесс."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import UTC, datetime

import asyncpg

from app.persistence.pending_web_links import cleanup_expired_links

_LOGGER = logging.getLogger(__name__)


async def reconcile_attempts(pool, *, limit: int = 100):
    from app.cryptobot.client import CryptoBotClient
    from app.cryptobot.payload import decode_payment_context
    from app.runtime.payment_webhook_helpers import amount_value_to_kopecks, fulfill_subscription_payment
    from app.yookassa.client import YooKassaClient
    from app.issuance.xui_vless_provider import XuiVlessProvider

    rows = await pool.fetch(
        """SELECT p.* FROM pending_web_payment_attempts p JOIN pending_web_links l USING(internal_user_id)
           WHERE p.status IN ('creating','pending','succeeded') AND p.provider_payment_id IS NOT NULL
           AND p.fulfilled_at IS NULL
           ORDER BY p.updated_at LIMIT $1""", limit,
    )
    for row in rows:
        client = None
        vless_provider = None
        try:
            provider = row["provider"]
            client = YooKassaClient.from_env() if provider == "yookassa" else CryptoBotClient.from_env()
            if client is None:
                continue
            info = (await client.get_payment(row["provider_payment_id"]) if provider == "yookassa"
                    else await client.get_invoice(row["provider_payment_id"]))
            if info is None:
                continue
            metadata = info.metadata if provider == "yookassa" else decode_payment_context(info.payload)
            if not isinstance(metadata, dict) or metadata.get("pending_web_attempt") != row["attempt_id"]:
                continue
            if info.status in ("succeeded", "paid"):
                amount = amount_value_to_kopecks(info.amount_value if provider == "yookassa" else info.fiat_amount)
                if amount != row["amount_kopecks"] or (provider == "cryptobot" and info.fiat != "RUB"):
                    continue
                # Используем тот же UC-04/UC-05, что и проверенный webhook.
                vless_provider = XuiVlessProvider(pool)
                result = await fulfill_subscription_payment(pool=pool,
                    vless_provider=vless_provider, activation_telegram_notifier=None, telemetry=None,
                    provider_key=os.environ.get(f"{provider.upper()}_PROVIDER_KEY", f"{provider}_v1"),
                    external_event_id=f"{provider}:{row['provider_payment_id']}",
                    external_payment_id=row["provider_payment_id"], metadata=metadata,
                    paid_amount_kopecks=amount, paid_at=datetime.now(UTC))
                if result.status_code < 300:
                    # Оплата исполнена: запись доказательства остаётся, повторная сверка не нужна.
                    await pool.execute("UPDATE pending_web_payment_attempts SET fulfilled_at=now(),updated_at=now() WHERE attempt_id=$1", row["attempt_id"])
            elif info.status in ("canceled", "expired"):
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.fetchrow("SELECT internal_user_id FROM pending_web_links WHERE internal_user_id=$1 FOR UPDATE", row["internal_user_id"])
                        await conn.execute("UPDATE pending_web_payment_attempts SET status='canceled',updated_at=now() WHERE attempt_id=$1 AND status IN ('creating','pending')", row["attempt_id"])
        except Exception:
            _LOGGER.warning("pending_web_payment_reconciliation_failed")
        finally:
            # Продвигаем очередь даже при временной ошибке, чтобы не застрять на первых 100 записях.
            await pool.execute("UPDATE pending_web_payment_attempts SET updated_at=now() WHERE attempt_id=$1", row["attempt_id"])
            if client is not None:
                await client._client.aclose()
            if vless_provider is not None:
                await vless_provider.aclose()


async def run(*, delete: bool, interval: int):
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        while True:
            # Один worker во всём кластере; блокировка привязана к соединению.
            async with pool.acquire() as conn:
                locked = await conn.fetchval("SELECT pg_try_advisory_lock(730093048)")
                if locked:
                    try:
                        if delete:
                            await reconcile_attempts(pool)
                        count = await cleanup_expired_links(pool, delete=delete)
                        unresolved = await pool.fetchval("SELECT count(*) FROM pending_web_payment_attempts WHERE status='creating' AND provider_payment_id IS NULL AND accepted_at < now()-interval '48 hours'")
                        print(json.dumps({"delete": delete, "eligible": count, "unknown_payments_over_48h": unresolved}), flush=True)
                    finally:
                        await conn.execute("SELECT pg_advisory_unlock(730093048)")
            if interval <= 0:
                break
            await asyncio.sleep(max(interval, 60))
    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description="Очистка временных неоплаченных ссылок сайта")
    parser.add_argument("--delete", action="store_true", help="Удаление после отдельного dry-run")
    parser.add_argument("--interval", type=int, default=0, help="Повторять с интервалом в секундах; 0 — однократно")
    args = parser.parse_args()
    if args.delete and os.environ.get("PENDING_WEB_LINK_DELETE_ENABLE") != "1":
        parser.error("Нужно отдельное включение PENDING_WEB_LINK_DELETE_ENABLE=1 после dry-run")
    asyncio.run(run(delete=args.delete, interval=args.interval))


if __name__ == "__main__":
    main()
