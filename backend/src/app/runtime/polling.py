"""Minimal slice-1 long-polling runtime shell (orchestration only, no SDK, no loop)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.application.bootstrap import Slice1Composition
from app.bot_transport import (
    TelegramRuntimeActionKind,
    handle_slice1_telegram_update_to_runtime_action,
)


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
    ) -> int:
        """Send one outbound text message; failures are reported via exceptions.

        Returns Telegram ``message_id`` from a successful Bot API ``sendMessage`` response.
        """
        ...

    async def answer_callback_query(self, callback_query_id: str) -> None:
        """Dismiss the inline button loading indicator via Telegram ``answerCallbackQuery``."""
        ...


def _extract_callback_query_id(update: Mapping[str, Any]) -> str | None:
    """Extract callback_query.id from a raw Telegram update (transport-level only)."""
    cq = update.get("callback_query")
    if isinstance(cq, Mapping):
        cb_id = cq.get("id")
        if isinstance(cb_id, str) and cb_id:
            return cb_id
    return None


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
                try:
                    await self._client.answer_callback_query(cb_qid)
                except Exception:
                    pass
            if action.kind is TelegramRuntimeActionKind.NOOP:
                noop += 1
                continue
            idem_key = action.uc01_idempotency_key
            sends: list[tuple[str, Mapping[str, Any] | None]] = [
                (action.message_text or "", action.reply_markup),
            ]
            sends.extend((fu.message_text, fu.reply_markup) for fu in action.follow_ups)
            try:
                if idem_key is not None:
                    await self._composition.outbound_delivery.ensure_pending(idem_key)
                first = True
                for text, markup in sends:
                    if not text.strip():
                        continue
                    msg_id = await self._client.send_text_message(
                        action.chat_id,
                        text,
                        correlation_id=action.correlation_id,
                        reply_markup=markup,
                    )
                    if first and idem_key is not None:
                        await self._composition.outbound_delivery.mark_sent(idem_key, msg_id)
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
