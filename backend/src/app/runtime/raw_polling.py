"""Thin raw-fetch shell: ``fetch_raw_updates`` → bridge → existing :class:`Slice1PollingRuntime` batch path."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable

from app.application.bootstrap import Slice1Composition
from app.application.telegram_update_dedup import TelegramUpdateDedupCommandBucket
from app.runtime.binding import process_raw_updates_with_bridge
from app.runtime.bridge import RuntimeUpdateBridge
from app.runtime.offsets import advance_polling_offset
from app.runtime.polling import PollingBatchResult, PollingRuntimeConfig, Slice1PollingRuntime


@dataclass(frozen=True, slots=True)
class RawPollingBatchResult:
    """Aggregated counters for one raw fetch + bridge + runtime batch (no payloads)."""

    raw_received_count: int
    bridge_accepted_count: int
    bridge_rejected_count: int
    bridge_exception_count: int
    send_count: int
    noop_count: int
    send_failure_count: int
    processing_failure_count: int
    fetch_failure_count: int


@runtime_checkable
class TelegramRawPollingClient(Protocol):
    """Raw transport batch fetch + outbound text send (no Telegram client-library types)."""

    async def fetch_raw_updates(
        self,
        *,
        limit: int,
        offset: int | None = None,
    ) -> Sequence[object]:
        """Return a bounded batch of opaque raw updates."""
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
    ) -> int: ...

    async def send_video(
        self,
        chat_id: int,
        video_path: str,
        *,
        correlation_id: str,
        caption: str | None = None,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> int: ...

    async def answer_callback_query(self, callback_query_id: str) -> None: ...

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> int: ...


def _mappings_for_offset(raw_updates: Sequence[object]) -> Sequence[Mapping[str, object]]:
    return tuple(cast("Mapping[str, object]", u) for u in raw_updates if isinstance(u, Mapping))


class _PollingClientFromRaw:
    """Adapts :class:`TelegramRawPollingClient` to :class:`TelegramPollingClient` for inner runtime."""

    __slots__ = ("_get_offset", "_raw")

    def __init__(
        self,
        raw: TelegramRawPollingClient,
        get_offset: Callable[[], int | None],
    ) -> None:
        self._raw = raw
        self._get_offset = get_offset

    async def fetch_updates(self, *, limit: int) -> Sequence[Mapping[str, Any]]:
        out = await self._raw.fetch_raw_updates(limit=limit, offset=self._get_offset())
        return cast("Sequence[Mapping[str, Any]]", tuple(out))

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
        return await self._raw.send_text_message(
            chat_id,
            text,
            correlation_id=correlation_id,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )

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
        return await self._raw.send_video(
            chat_id,
            video_path,
            correlation_id=correlation_id,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    async def answer_callback_query(self, callback_query_id: str) -> None:
        await self._raw.answer_callback_query(callback_query_id)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> int:
        return await self._raw.edit_message_text(
            chat_id,
            message_id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )


class Slice1RawPollingRuntime:
    """One-step raw fetch, then :func:`process_raw_updates_with_bridge` on inner :class:`Slice1PollingRuntime`."""

    __slots__ = ("_bridge", "_composition", "_config", "_current_offset", "_inner", "_raw_client")

    def __init__(
        self,
        composition: Slice1Composition,
        client: TelegramRawPollingClient,
        bridge: RuntimeUpdateBridge,
        *,
        config: PollingRuntimeConfig | None = None,
    ) -> None:
        self._composition = composition
        self._raw_client = client
        self._bridge = bridge
        self._config = config or PollingRuntimeConfig()
        self._current_offset: int | None = None
        adapter = _PollingClientFromRaw(client, lambda: self._current_offset)
        self._inner = Slice1PollingRuntime(composition, adapter, config=self._config)

    @property
    def current_offset(self) -> int | None:
        return self._current_offset

    async def mark_update_first_seen(
        self,
        *,
        namespace: str,
        command_bucket: TelegramUpdateDedupCommandBucket,
        telegram_update_id: int,
    ) -> bool:
        return await self._composition.telegram_update_dedup.mark_if_first_seen(
            namespace=namespace,
            command_bucket=command_bucket,
            telegram_update_id=telegram_update_id,
        )

    async def process_single_mapped_update(
        self,
        update: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> PollingBatchResult:
        """Process one Telegram-shaped mapping without ``getUpdates`` (push-style HTTP ingress)."""
        return await self._inner.process_single_update(update, correlation_id=correlation_id)

    async def poll_once(self, *, correlation_id: str | None = None) -> RawPollingBatchResult:
        limit = self._config.max_updates_per_batch
        try:
            raw_updates = await self._raw_client.fetch_raw_updates(
                limit=limit,
                offset=self._current_offset,
            )
        except Exception:
            return RawPollingBatchResult(
                raw_received_count=0,
                bridge_accepted_count=0,
                bridge_rejected_count=0,
                bridge_exception_count=0,
                send_count=0,
                noop_count=0,
                send_failure_count=0,
                processing_failure_count=0,
                fetch_failure_count=1,
            )
        self._current_offset = advance_polling_offset(
            self._current_offset,
            _mappings_for_offset(raw_updates),
        )
        bound = await process_raw_updates_with_bridge(
            self._inner,
            raw_updates,
            self._bridge,
            correlation_id=correlation_id,
        )
        return RawPollingBatchResult(
            raw_received_count=bound.raw_received_count,
            bridge_accepted_count=bound.bridge_accepted_count,
            bridge_rejected_count=bound.bridge_rejected_count,
            bridge_exception_count=bound.bridge_exception_count,
            send_count=bound.send_count,
            noop_count=bound.noop_count,
            send_failure_count=bound.send_failure_count,
            processing_failure_count=bound.processing_failure_count,
            fetch_failure_count=0,
        )
