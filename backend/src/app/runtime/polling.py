"""Minimal slice-1 long-polling runtime shell (orchestration only, no SDK, no loop)."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.application.bootstrap import Slice1Composition
from app.bot_transport import (
    TelegramRuntimeActionKind,
    handle_slice1_telegram_update_to_runtime_action,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PollingRuntimeConfig:
    """Bounded slice-1 polling shell parameters (no secrets, no tokens)."""

    max_updates_per_batch: int = 100


@dataclass(frozen=True, slots=True)
class PollingBatchResult:
    received_count: int
    send_count: int
    noop_count: int
    send_failure_count: int
    processing_failure_count: int
    fetch_failure_count: int = 0


@runtime_checkable
class TelegramPollingClient(Protocol):
    """Contract for a future SDK binding: fetch updates + send text (no Telegram types here)."""

    async def fetch_updates(self, *, limit: int) -> Sequence[Mapping[str, Any]]:
        """Return a batch of Telegram-like update mappings (bounded by ``limit``)."""
        ...

    async def send_text_message(
        self,
        chat_id: int,
        text: str,
        *,
        correlation_id: str,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> int:
        """Send one outbound text message; failures are reported via exceptions.

        Returns Telegram ``message_id`` from a successful Bot API ``sendMessage`` response.
        """
        ...

    async def send_video(
        self,
        chat_id: int,
        video_path: str,
        *,
        correlation_id: str,
        caption: str | None = None,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> int:
        """Send a video file to the chat. Returns Telegram ``message_id``."""
        ...

    async def send_photo(
        self,
        chat_id: int,
        photo_path: str,
        *,
        caption: str | None = None,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> int:
        """Send a photo file to the chat. Returns Telegram ``message_id``."""
        ...

    async def send_document(
        self,
        chat_id: int,
        document_path: str,
        *,
        caption: str | None = None,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> int:
        """Send a document file to the chat. Returns Telegram ``message_id``."""
        ...

    async def answer_callback_query(self, callback_query_id: str) -> None:
        """Dismiss the inline button loading indicator via Telegram ``answerCallbackQuery``."""
        ...

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> None:
        """Delete a message via Telegram ``deleteMessage``."""
        ...

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> int:
        """Update an existing message in-place via Telegram ``editMessageText``.

        Returns the ``message_id`` of the edited message.
        """
        ...


def _extract_callback_query_id(update: Mapping[str, Any]) -> str | None:
    """Extract callback_query.id from a raw Telegram update (transport-level only)."""
    cq = update.get("callback_query")
    if isinstance(cq, Mapping):
        cb_id = cq.get("id")
        if isinstance(cb_id, str) and cb_id:
            return cb_id
    return None


def _extract_callback_origin_message(update: Mapping[str, Any]) -> tuple[int, int] | None:
    """Extract (chat_id, message_id) of the original message a callback_query belongs to."""
    cq = update.get("callback_query")
    if not isinstance(cq, Mapping):
        return None
    msg = cq.get("message")
    if not isinstance(msg, Mapping):
        return None
    chat = msg.get("chat")
    if not isinstance(chat, Mapping):
        return None
    chat_id = chat.get("id")
    message_id = msg.get("message_id")
    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return None
    return chat_id, message_id


class Slice1PollingRuntime:
    """Thin batch/single-update runner over :func:`handle_slice1_telegram_update_to_runtime_action`."""

    __slots__ = ("_client", "_composition", "_config")

    def __init__(
        self,
        composition: Slice1Composition,
        client: TelegramPollingClient,
        *,
        config: PollingRuntimeConfig | None = None,
    ) -> None:
        self._composition = composition
        self._client = client
        self._config = config or PollingRuntimeConfig()

    async def process_batch(
        self,
        updates: Sequence[Mapping[str, Any]],
        *,
        correlation_id: str | None = None,
    ) -> PollingBatchResult:
        capped = tuple(updates[: self._config.max_updates_per_batch])
        received = len(capped)
        send_ok = 0
        noop = 0
        send_fail = 0
        process_fail = 0
        for u in capped:
            cb_qid = _extract_callback_query_id(u)
            cb_origin = _extract_callback_origin_message(u)
            try:
                action = await handle_slice1_telegram_update_to_runtime_action(
                    u,
                    self._composition,
                    correlation_id=correlation_id,
                )
            except Exception:
                process_fail += 1
                continue
            if cb_qid is not None:
                with contextlib.suppress(Exception):
                    await self._client.answer_callback_query(cb_qid)
            if action.kind is TelegramRuntimeActionKind.NOOP:
                noop += 1
                continue
            idem_key = action.uc01_idempotency_key
            media_type = action.media_type
            media_path = action.media_path
            sends: list[tuple[str, Mapping[str, Any] | None, str | None]] = [
                (action.message_text or "", action.reply_markup, action.parse_mode),
            ]
            sends.extend((fu.message_text, fu.reply_markup, fu.parse_mode) for fu in action.follow_ups)
            try:
                if idem_key is not None:
                    await self._composition.outbound_delivery.ensure_pending(idem_key)
                first = True
                for text, markup, pmode in sends:
                    if not text.strip() and not (first and media_path):
                        continue
                    # Media attachment: video, photo, or document
                    if first and media_type and media_path:
                        # Delete old callback message to avoid clutter
                        if cb_origin is not None:
                            with contextlib.suppress(Exception):
                                await self._client.delete_message(cb_origin[0], cb_origin[1])
                        _send_method = {
                            "video": self._client.send_video,
                            "photo": self._client.send_photo,
                            "document": self._client.send_document,
                        }.get(media_type, self._client.send_photo)
                        try:
                            msg_id = await _send_method(
                                action.chat_id,
                                media_path,
                                caption=text if text.strip() else None,
                                reply_markup=markup,
                                parse_mode=pmode,
                            )
                            _LOGGER.info(
                                "polling.send_%s_ok chat_id=%s msg_id=%s",
                                media_type,
                                action.chat_id,
                                msg_id,
                            )
                        except Exception:
                            _LOGGER.warning(
                                "polling.send_%s_failed chat_id=%s -> fallback text",
                                media_type,
                                action.chat_id,
                            )
                            msg_id = await self._client.send_text_message(
                                action.chat_id,
                                text,
                                correlation_id=action.correlation_id,
                                reply_markup=markup,
                                parse_mode=pmode,
                            )
                    elif first and cb_origin is not None:
                        origin_chat_id, origin_msg_id = cb_origin
                        try:
                            msg_id = await self._client.edit_message_text(
                                origin_chat_id,
                                origin_msg_id,
                                text,
                                reply_markup=markup,
                                parse_mode=pmode,
                            )
                            _LOGGER.info(
                                "polling.edit_message_ok chat_id=%s msg_id=%s",
                                origin_chat_id,
                                origin_msg_id,
                            )
                        except Exception as exc:
                            _LOGGER.warning(
                                "polling.edit_message_failed chat_id=%s msg_id=%s error=%s -> fallback sendMessage",
                                origin_chat_id,
                                origin_msg_id,
                                exc,
                            )
                            msg_id = await self._client.send_text_message(
                                action.chat_id,
                                text,
                                correlation_id=action.correlation_id,
                                reply_markup=markup,
                                parse_mode=pmode,
                                disable_web_page_preview=action.disable_web_page_preview,
                            )
                    else:
                        if first and cb_origin is None and cb_qid is not None:
                            _LOGGER.info(
                                "polling.callback_no_origin -> sendMessage chat_id=%s",
                                action.chat_id,
                            )
                        msg_id = await self._client.send_text_message(
                            action.chat_id,
                            text,
                            correlation_id=action.correlation_id,
                            reply_markup=markup,
                            parse_mode=pmode,
                            disable_web_page_preview=action.disable_web_page_preview,
                        )
                    if first and idem_key is not None:
                        await self._composition.outbound_delivery.mark_sent(idem_key, msg_id)
                    if first:
                        from app.bot_transport.payment_message_registry import (
                            pop_pending_payment_for_user,
                            register_payment_message,
                        )

                        pending_pid = pop_pending_payment_for_user(action.chat_id)
                        if pending_pid is not None and msg_id is not None:
                            register_payment_message(pending_pid, action.chat_id, msg_id)
                    first = False
                    send_ok += 1
            except Exception:
                send_fail += 1
                continue
        return PollingBatchResult(
            received_count=received,
            send_count=send_ok,
            noop_count=noop,
            send_failure_count=send_fail,
            processing_failure_count=process_fail,
        )

    async def poll_once(self, *, correlation_id: str | None = None) -> PollingBatchResult:
        """One long-poll fetch step: single ``fetch_updates`` then :meth:`process_batch`."""
        limit = self._config.max_updates_per_batch
        try:
            updates = await self._client.fetch_updates(limit=limit)
        except Exception:
            return PollingBatchResult(
                received_count=0,
                send_count=0,
                noop_count=0,
                send_failure_count=0,
                processing_failure_count=0,
                fetch_failure_count=1,
            )
        return await self.process_batch(updates, correlation_id=correlation_id)

    async def process_single_update(
        self,
        update: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> PollingBatchResult:
        return await self.process_batch((update,), correlation_id=correlation_id)
