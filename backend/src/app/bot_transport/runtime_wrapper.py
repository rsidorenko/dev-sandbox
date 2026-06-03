"""Чистая обёртка runtime slice 1: маппинг Telegram-подобного update → действие отправки runtime (без SDK, без I/O).

Связывает сырые обновления с :func:`handle_slice1_telegram_update_to_rendered_message`, затем применяет
политику отправки/не-действия из архитектурных документов 17/18: целевой приватный чат + отрендеренный текст → отправка;
иначе не-действие. Не дублирует диспетчеризацию адаптера, нормализацию или логику приложения.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.application.bootstrap import Slice1Composition
from app.bot_transport.runtime_facade import handle_slice1_telegram_update_to_rendered_message
from app.security.validation import ValidationError, validate_telegram_user_id


class TelegramRuntimeActionKind(StrEnum):
    """Минимальный исходящий интент runtime для slice 1 (транспорт-агностичный, без SDK)."""

    SEND_MESSAGE = "send_message"
    NOOP = "noop"


@dataclass(frozen=True, slots=True)
class TelegramRuntimeFollowUpSend:
    """Дополнительный исходящий текст после основного отрендеренного сообщения (тот же update, тот же чат)."""

    message_text: str
    reply_markup: Mapping[str, Any] | None
    parse_mode: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramRuntimeAction:
    """Единичное исходящее действие, полученное из отрендеренного пакета; без сырой Telegram-нагрузки."""

    kind: TelegramRuntimeActionKind
    correlation_id: str
    chat_id: int | None
    message_text: str | None
    action_keys: tuple[str, ...]
    reply_markup: Mapping[str, Any] | None
    uc01_idempotency_key: str | None = None
    follow_ups: tuple[TelegramRuntimeFollowUpSend, ...] = ()
    parse_mode: str | None = None
    video_path: str | None = None
    disable_web_page_preview: bool = False


def extract_eligible_private_chat_id_from_telegram_like_update(
    update: Mapping[str, Any],
) -> int | None:
    """
    Извлечение исходящего chat id с fail-closed для slice 1.

    Поддерживает message (текстовые команды) и callback_query (inline-кнопки).
    Валидирует только структурную идентичность приватного чата.
    """
    if not isinstance(update, Mapping):
        return None

    # Handle callback_query (inline button presses)
    cq = update.get("callback_query")
    if isinstance(cq, Mapping):
        from_user = cq.get("from")
        if not isinstance(from_user, Mapping):
            return None
        try:
            return validate_telegram_user_id(from_user.get("id"))
        except (ValidationError, TypeError):
            return None

    # Handle regular message
    message = update.get("message")
    if not isinstance(message, Mapping):
        return None
    chat = message.get("chat")
    if not isinstance(chat, Mapping):
        return None
    if chat.get("type") != "private":
        return None
    from_user = message.get("from")
    if not isinstance(from_user, Mapping):
        return None
    raw_chat_id = chat.get("id")
    raw_user_id = from_user.get("id")
    try:
        chat_id = validate_telegram_user_id(raw_chat_id)
        user_id = validate_telegram_user_id(raw_user_id)
    except (ValidationError, TypeError):
        return None
    if chat_id != user_id:
        return None
    return chat_id


async def handle_slice1_telegram_update_to_runtime_action(
    update: Mapping[str, Any],
    composition: Slice1Composition,
    *,
    correlation_id: str | None = None,
) -> TelegramRuntimeAction:
    """
    Сырой Telegram-подобный update → существующий facade runtime → одно :class:`TelegramRuntimeAction`.

    Correlation id действия всегда берётся из отрендеренного пакета (истина пайплайна).
    Ожидаемые пути адаптера/сервиса не выбрасывают исключения; ошибки выражаются как безопасный отрендеренный текст.
    """
    rendered = await handle_slice1_telegram_update_to_rendered_message(
        update,
        composition,
        correlation_id=correlation_id,
    )
    cid = rendered.correlation_id
    target = extract_eligible_private_chat_id_from_telegram_like_update(update)
    idem_key = rendered.uc01_idempotency_key
    ledger = composition.outbound_delivery
    if rendered.replay_suppresses_outbound:
        if not idem_key or ledger is None:
            return TelegramRuntimeAction(
                kind=TelegramRuntimeActionKind.NOOP,
                correlation_id=cid,
                chat_id=None,
                message_text=None,
                action_keys=(),
                reply_markup=None,
                uc01_idempotency_key=None,
                follow_ups=(),
            )
        rec = await ledger.get_status(idem_key)
        if rec is not None and rec.status == "sent" and rec.telegram_message_id is not None:
            return TelegramRuntimeAction(
                kind=TelegramRuntimeActionKind.NOOP,
                correlation_id=cid,
                chat_id=None,
                message_text=None,
                action_keys=(),
                reply_markup=None,
                uc01_idempotency_key=None,
                follow_ups=(),
            )
        if rec is None or rec.status != "pending":
            return TelegramRuntimeAction(
                kind=TelegramRuntimeActionKind.NOOP,
                correlation_id=cid,
                chat_id=None,
                message_text=None,
                action_keys=(),
                reply_markup=None,
                uc01_idempotency_key=None,
                follow_ups=(),
            )
    if target is None or not rendered.message_text.strip():
        return TelegramRuntimeAction(
            kind=TelegramRuntimeActionKind.NOOP,
            correlation_id=cid,
            chat_id=None,
            message_text=None,
            action_keys=(),
            reply_markup=None,
            uc01_idempotency_key=None,
            follow_ups=(),
        )
    follow_ups = tuple(
        TelegramRuntimeFollowUpSend(message_text=fu.message_text, reply_markup=fu.reply_markup, parse_mode=fu.parse_mode)
        for fu in rendered.follow_up_messages
    )
    return TelegramRuntimeAction(
        kind=TelegramRuntimeActionKind.SEND_MESSAGE,
        correlation_id=cid,
        chat_id=target,
        message_text=rendered.message_text,
        action_keys=rendered.action_keys,
        reply_markup=rendered.reply_markup,
        uc01_idempotency_key=idem_key,
        follow_ups=follow_ups,
        parse_mode=rendered.parse_mode,
        video_path=rendered.video_path,
        disable_web_page_preview=rendered.disable_web_page_preview,
    )


class Slice1TelegramRuntimeWrapper:
    """Хранит :class:`Slice1Composition` и делегирует :func:`handle_slice1_telegram_update_to_runtime_action`."""

    __slots__ = ("_composition",)

    def __init__(self, composition: Slice1Composition) -> None:
        self._composition = composition

    async def handle(
        self,
        update: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> TelegramRuntimeAction:
        return await handle_slice1_telegram_update_to_runtime_action(
            update,
            self._composition,
            correlation_id=correlation_id,
        )

    async def dispatch(
        self,
        update: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> TelegramRuntimeAction:
        return await self.handle(update, correlation_id=correlation_id)
