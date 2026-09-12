"""Контракт и безопасная нормализация first-touch атрибуции Telegram Ads."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Protocol

_LOGGER = logging.getLogger(__name__)
_CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_START_PREFIXES = ("tgads_", "ads_")


def parse_telegram_ads_campaign(start_param: str | None) -> str | None:
    """Возвращает нормализованный campaign key или None для чужого/malformed параметра."""
    if not isinstance(start_param, str):
        return None
    normalized = start_param.strip().lower()
    prefix = next((candidate for candidate in _START_PREFIXES if normalized.startswith(candidate)), None)
    if prefix is None:
        return None
    campaign = normalized[len(prefix) :]
    return campaign if _CAMPAIGN_RE.fullmatch(campaign) else None


@dataclass(frozen=True, slots=True)
class TelegramAdsAttributionRecord:
    internal_user_id: str
    campaign_key: str
    telegram_update_id: int


class TelegramAdsAttributionRecorder(Protocol):
    async def record_first_touch(self, record: TelegramAdsAttributionRecord) -> bool:
        """True, только если first-touch создан этим вызовом."""


class InMemoryTelegramAdsAttributionRecorder:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_user: dict[str, TelegramAdsAttributionRecord] = {}

    async def record_first_touch(self, record: TelegramAdsAttributionRecord) -> bool:
        async with self._lock:
            if record.internal_user_id in self._by_user:
                return False
            self._by_user[record.internal_user_id] = record
            return True

    async def get_for_user(self, internal_user_id: str) -> TelegramAdsAttributionRecord | None:
        async with self._lock:
            return self._by_user.get(internal_user_id)


async def safely_record_telegram_ads_first_touch(
    recorder: TelegramAdsAttributionRecorder,
    *,
    internal_user_id: str,
    campaign_key: str,
    telegram_update_id: int,
) -> bool:
    """Аналитическая ошибка не меняет результат регистрации; CancelledError проходит наружу."""
    try:
        async with asyncio.timeout(0.2):
            return await recorder.record_first_touch(
                TelegramAdsAttributionRecord(
                    internal_user_id=internal_user_id,
                    campaign_key=campaign_key,
                    telegram_update_id=telegram_update_id,
                )
            )
    except Exception:
        # Campaign/user не логируем: это пользовательский ввод и идентификатор.
        _LOGGER.warning("telegram_ads_attribution_write_failed")
        return False
