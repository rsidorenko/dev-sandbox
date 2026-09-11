"""PostgreSQL first-touch репозиторий атрибуции Telegram Ads."""

from __future__ import annotations

import asyncpg

from app.application.telegram_ads_attribution import TelegramAdsAttributionRecord


class PostgresTelegramAdsAttributionRecorder:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_first_touch(self, record: TelegramAdsAttributionRecord) -> bool:
        status = await self._pool.execute(
            "INSERT INTO telegram_ads_attributions "
            "(internal_user_id, campaign_key, telegram_update_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (internal_user_id) DO NOTHING",
            record.internal_user_id,
            record.campaign_key,
            record.telegram_update_id,
        )
        return status == "INSERT 0 1"
