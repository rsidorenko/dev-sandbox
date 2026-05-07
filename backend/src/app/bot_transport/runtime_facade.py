"""Тонкий facade runtime slice 1: сырой Telegram-подобный update → пакет отрендеренного сообщения (без SDK, без сервера).

Оркестрирует adapter → service/dispatch → outbound keys → рендер каталога сообщений.
Сырые обновления не пересекают границу адаптера; этот модуль не принимает типы Telegram SDK.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from app.application.bootstrap import Slice1Composition
from app.bot_transport.message_catalog import RenderedMessagePackage, render_telegram_outbound_plan
from app.bot_transport.outbound import (
    build_subscription_active_recovery_confirmation_plan,
    map_transport_safe_to_outbound_plan,
)
from app.bot_transport.service import handle_slice1_telegram_update
from app.security.validation import ValidationError, validate_telegram_user_id


def _extract_private_telegram_user_id(update: Mapping[str, Any]) -> int | None:
    message = update.get("message")
    if not isinstance(message, Mapping):
        return None
    chat = message.get("chat")
    if not isinstance(chat, Mapping) or chat.get("type") != "private":
        return None
    from_user = message.get("from")
    if not isinstance(from_user, Mapping):
        return None
    try:
        chat_id = validate_telegram_user_id(chat.get("id"))
        from_id = validate_telegram_user_id(from_user.get("id"))
    except (ValidationError, TypeError):
        return None
    if chat_id != from_id:
        return None
    return from_id


async def handle_slice1_telegram_update_to_rendered_message(
    update: Mapping[str, Any],
    composition: Slice1Composition,
    *,
    correlation_id: str | None = None,
) -> RenderedMessagePackage:
    """
    Полный пайплайн slice 1 до пользовательского текста: извлечение → диспетчеризация → исходящий план → рендер каталога.

    Отклонение адаптера и безопасные ошибки уровня обработчика возвращают стабильный :class:`RenderedMessagePackage`
    (без исключений для ожидаемых классов ошибок).
    """
    transport = await handle_slice1_telegram_update(
        update,
        composition,
        correlation_id=correlation_id,
    )
    plan = map_transport_safe_to_outbound_plan(transport)
    uid = _extract_private_telegram_user_id(update)
    primary = render_telegram_outbound_plan(plan, telegram_user_id=uid)
    if not transport.subscription_active_recovery_followup:
        return primary
    confirm = render_telegram_outbound_plan(
        build_subscription_active_recovery_confirmation_plan(transport),
        telegram_user_id=uid,
    )
    return replace(primary, follow_up_messages=(confirm,))


class Slice1TelegramRuntimeFacade:
    """Вызываемая обёртка для :func:`handle_slice1_telegram_update_to_rendered_message`."""

    __slots__ = ()

    async def handle_update_to_rendered_message(
        self,
        update: Mapping[str, Any],
        composition: Slice1Composition,
        *,
        correlation_id: str | None = None,
    ) -> RenderedMessagePackage:
        return await handle_slice1_telegram_update_to_rendered_message(
            update,
            composition,
            correlation_id=correlation_id,
        )
