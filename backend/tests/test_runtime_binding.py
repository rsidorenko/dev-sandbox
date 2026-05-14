"""Tests for runtime bridge → :class:`Slice1PollingRuntime` binding seam."""

from __future__ import annotations

from app.application.bootstrap import build_slice1_composition
from app.runtime.binding import BoundRuntimeBatchResult, process_raw_updates_with_bridge
from app.runtime.polling import Slice1PollingRuntime
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


def _base_message(*, text: str, user_id: int = 42, chat_type: str = "private") -> dict[str, object]:
    return {
        "message_id": 1,
        "from": {"id": user_id, "is_bot": False, "first_name": "U"},
        "chat": {"id": user_id, "type": chat_type},
        "text": text,
    }


def _update(
    *,
    update_id: int = 1,
    message: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    u: dict[str, object] = {"update_id": update_id, "message": message}
    u.update(extra)
    return u


class FakeTelegramPollingClient:
    """In-memory double: records sends."""

    __slots__ = ("fetch_calls", "last_fetch_limit", "send_calls", "send_fail")

    def __init__(self) -> None:
        self.fetch_calls = 0
        self.last_fetch_limit: int | None = None
        self.send_calls: list[tuple[int, str, str]] = []
        self.send_fail = False

    async def fetch_updates(self, *, limit: int):
        self.fetch_calls += 1
        self.last_fetch_limit = limit
        return ()

    async def send_text_message(
        self,
        chat_id: int,
        text: str,
        *,
        correlation_id: str,
        reply_markup: object | None = None,
    ) -> int:
        if self.send_fail:
            raise RuntimeError("send failed")
        self.send_calls.append((chat_id, text, correlation_id))
        return 1

    async def answer_callback_query(self, callback_query_id: str) -> None:
        pass

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup=None,
    ) -> int:
        return message_id


def test_two_valid_raw_updates_identity_bridge_aggregates_counters() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        rt = Slice1PollingRuntime(c, client)
        u1 = _update(update_id=1, message=_base_message(text="/start"))
        u2 = _update(update_id=2, message=_base_message(user_id=7, text="/start"))

        def identity_bridge(raw: object):
            return raw if isinstance(raw, dict) else None

        r = await process_raw_updates_with_bridge(rt, [u1, u2], identity_bridge)
        assert r == BoundRuntimeBatchResult(
            raw_received_count=2,
            bridge_accepted_count=2,
            bridge_rejected_count=0,
            bridge_exception_count=0,
            send_count=2,
            noop_count=0,
            send_failure_count=0,
            processing_failure_count=0,
        )
        assert len(client.send_calls) == 2

    _run(main())


def test_mixed_batch_accepted_rejected_bridge_exception() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        rt = Slice1PollingRuntime(c, client)
        good = _update(update_id=10, message=_base_message(user_id=10, text="/start"))
        rejected = {"update_id": 11, "_skip": True}
        bad = {"update_id": 12, "_exc": True}

        def bridge(raw: object) -> dict[str, object] | None:
            if not isinstance(raw, dict):
                return None
            if raw.get("_exc"):
                raise RuntimeError("bridge item failed")
            if raw.get("_skip"):
                return None
            return raw

        r = await process_raw_updates_with_bridge(rt, [good, rejected, bad], bridge)
        assert r.raw_received_count == 3
        assert r.bridge_accepted_count == 1
        assert r.bridge_rejected_count == 1
        assert r.bridge_exception_count == 1
        assert r.send_count == 1
        assert r.noop_count == 0
        assert client.send_calls[0][0] == 10

    _run(main())


def test_no_accepted_skips_runtime_zero_counters_no_send() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        rt = Slice1PollingRuntime(c, client)

        r = await process_raw_updates_with_bridge(
            rt,
            [{"x": 1}, {"y": 2}],
            lambda _: None,
            correlation_id=new_correlation_id(),
        )
        assert r == BoundRuntimeBatchResult(
            raw_received_count=2,
            bridge_accepted_count=0,
            bridge_rejected_count=2,
            bridge_exception_count=0,
            send_count=0,
            noop_count=0,
            send_failure_count=0,
            processing_failure_count=0,
        )
        assert client.send_calls == []

    _run(main())


def test_correlation_id_reaches_send_path() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        rt = Slice1PollingRuntime(c, client)
        u = _update(message=_base_message(text="/start"))
        cid = new_correlation_id()

        def identity_bridge(raw: object):
            return raw if isinstance(raw, dict) else None

        await process_raw_updates_with_bridge(rt, [u], identity_bridge, correlation_id=cid)
        assert len(client.send_calls) == 1
        assert client.send_calls[0][2] == cid

    _run(main())
