"""Тонкая точка входа сервиса slice 1: сырой Telegram-подобный update → адаптер → диспетчер (без SDK, без runtime)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.application.bootstrap import Slice1Composition
from app.bot_transport.dispatcher import dispatch_slice1_transport
from app.bot_transport.presentation import (
    TransportErrorCode,
    TransportResponseCategory,
    TransportSafeResponse,
)
from app.bot_transport.telegram_adapter import (
    TelegramAdapterRejected,
    extract_slice1_envelope_from_telegram_update,
)


def _adapter_reject_to_transport_safe(rejected: TelegramAdapterRejected) -> TransportSafeResponse:
    """Стабильное пользовательское маппинг; не раскрывает коды причин адаптера в транспорте."""
    return TransportSafeResponse(
        category=TransportResponseCategory.ERROR,
        code=TransportErrorCode.INVALID_INPUT.value,
        correlation_id=rejected.correlation_id,
        next_action_hint=None,
        uc01_idempotency_key=None,
    )


async def handle_slice1_telegram_update(
    update: Mapping[str, Any],
    composition: Slice1Composition,
    *,
    correlation_id: str | None = None,
) -> TransportSafeResponse:
    """
    Маппинг сырого Telegram-подобного update → извлечение обёртки → диспетчеризация UC-01/UC-02.

    Сырая нагрузка не проходит границу адаптера; отклонение адаптера возвращает безопасный
    транспортный ответ (без исключения, без внутренней причины в теле ответа).
    """
    extracted = extract_slice1_envelope_from_telegram_update(
        update,
        correlation_id=correlation_id,
    )
    if isinstance(extracted, TelegramAdapterRejected):
        return _adapter_reject_to_transport_safe(extracted)
    return await dispatch_slice1_transport(extracted, composition)


class Slice1TelegramService:
    """Тонкая вызываемая обёртка для :func:`handle_slice1_telegram_update` (опциональный хост композиции)."""

    __slots__ = ()

    async def handle_telegram_update(
        self,
        update: Mapping[str, Any],
        composition: Slice1Composition,
        *,
        correlation_id: str | None = None,
    ) -> TransportSafeResponse:
        return await handle_slice1_telegram_update(
            update,
            composition,
            correlation_id=correlation_id,
        )
