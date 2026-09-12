"""PostgreSQL-хранилище ручного дневного расхода Telegram Ads."""

from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg

_SPEND_WRITE_LOCK_ID = 2_026_091_201


class PostgresTelegramAdsSpendRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def set_daily_spend(
        self,
        *,
        campaign_key: str,
        spend_date: date,
        amount_kopecks: int,
        actor: str,
    ) -> dict[str, Any]:
        """Задать сумму за день; ноль очищает строку.

        Глобальный xact-lock сериализует редкие admin-записи. Повтор того же
        состояния не обновляет timestamp и не раздувает audit trail.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _SPEND_WRITE_LOCK_ID)
            old = await conn.fetchrow(
                "SELECT campaign_key, spend_date, amount_kopecks, updated_by, updated_at "
                "FROM telegram_ads_spend_daily WHERE campaign_key = $1 AND spend_date = $2",
                campaign_key,
                spend_date,
            )
            old_amount = int(old["amount_kopecks"]) if old is not None else None

            if amount_kopecks == 0:
                if old is None:
                    return {
                        "campaign_key": campaign_key,
                        "spend_date": spend_date,
                        "amount_kopecks": 0,
                        "updated_by": actor,
                        "updated_at": None,
                        "changed": False,
                    }
                await conn.execute(
                    "DELETE FROM telegram_ads_spend_daily WHERE campaign_key = $1 AND spend_date = $2",
                    campaign_key,
                    spend_date,
                )
                await self._record_action(
                    conn,
                    campaign_key=campaign_key,
                    spend_date=spend_date,
                    action="clear",
                    old_amount_kopecks=old_amount,
                    new_amount_kopecks=None,
                    actor=actor,
                )
                return {
                    "campaign_key": campaign_key,
                    "spend_date": spend_date,
                    "amount_kopecks": 0,
                    "updated_by": actor,
                    "updated_at": None,
                    "changed": True,
                }

            if old_amount == amount_kopecks:
                result = dict(old)
                result["changed"] = False
                return result

            row = await conn.fetchrow(
                "INSERT INTO telegram_ads_spend_daily "
                "(campaign_key, spend_date, amount_kopecks, updated_by) VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (campaign_key, spend_date) DO UPDATE SET "
                "amount_kopecks = EXCLUDED.amount_kopecks, updated_by = EXCLUDED.updated_by, updated_at = NOW() "
                "RETURNING campaign_key, spend_date, amount_kopecks, updated_by, updated_at",
                campaign_key,
                spend_date,
                amount_kopecks,
                actor,
            )
            assert row is not None
            await self._record_action(
                conn,
                campaign_key=campaign_key,
                spend_date=spend_date,
                action="set",
                old_amount_kopecks=old_amount,
                new_amount_kopecks=amount_kopecks,
                actor=actor,
            )
            result = dict(row)
            result["changed"] = True
            return result

    @staticmethod
    async def _record_action(
        conn: asyncpg.Connection,
        *,
        campaign_key: str,
        spend_date: date,
        action: str,
        old_amount_kopecks: int | None,
        new_amount_kopecks: int | None,
        actor: str,
    ) -> None:
        await conn.execute(
            "INSERT INTO telegram_ads_spend_actions "
            "(campaign_key, spend_date, action, old_amount_kopecks, new_amount_kopecks, actor) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            campaign_key,
            spend_date,
            action,
            old_amount_kopecks,
            new_amount_kopecks,
            actor,
        )
