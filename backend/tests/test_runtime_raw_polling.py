"""Tests for :mod:`app.runtime.raw_polling` (raw fetch shell, no SDK)."""

from __future__ import annotations

import inspect

import app.runtime.raw_polling as raw_polling_mod
from app.application.bootstrap import build_slice1_composition
from app.runtime.binding import process_raw_updates_with_bridge
from app.runtime.raw_polling import RawPollingBatchResult, Slice1RawPollingRuntime, TelegramRawPollingClient
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


def _identity_bridge(raw: object):
    return raw if isinstance(raw, dict) else None


class FakeTelegramRawPollingClient:
    """In-memory raw client: ``fetch_raw_updates`` + recorded sends."""

    __slots__ = (
        "_fetch_queue",
        "_fetch_raises",
        "_fetch_round_idx",
        "_fetch_rounds",
        "fetch_calls",
        "fetch_offset_history",
        "last_fetch_limit",
        "last_fetch_offset",
        "send_calls",
        "send_fail",
    )

    def __init__(
        self,
        fetch_queue: list[dict[str, object]] | None = None,
        *,
        fetch_rounds: list[list[dict[str, object]]] | None = None,
    ) -> None:
        self._fetch_queue = list(fetch_queue or ())
        self._fetch_rounds = list(fetch_rounds) if fetch_rounds is not None else None
        self._fetch_round_idx = 0
        self.fetch_calls = 0
        self.last_fetch_limit: int | None = None
        self.last_fetch_offset: int | None = None
        self.fetch_offset_history: list[int | None] = []
        self.send_calls: list[tuple[int, str, str]] = []
        self.send_fail = False
        self._fetch_raises: BaseException | None = None

    def set_fetch_raises(self, exc: BaseException) -> None:
        self._fetch_raises = exc

    async def fetch_raw_updates(self, *, limit: int, offset: int | None = None):
        self.fetch_calls += 1
        self.last_fetch_limit = limit
        self.last_fetch_offset = offset
        self.fetch_offset_history.append(offset)
        if self._fetch_raises is not None:
            raise self._fetch_raises
        if self._fetch_rounds is not None:
            if self._fetch_round_idx < len(self._fetch_rounds):
                batch = self._fetch_rounds[self._fetch_round_idx]
                self._fetch_round_idx += 1
            else:
                batch = []
            return list(batch)
        return list(self._fetch_queue)

    async def send_text_message(
        self,
        chat_id: int,
        text: str,
        *,
        correlation_id: str,
        reply_markup: object | None = None,
        parse_mode: str | None = None,
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
        parse_mode: str | None = None,
    ) -> int:
        return message_id


def test_one_raw_start_fetch_bridge_send_count_one() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        raw = _update(message=_base_message(text="/start"))
        client = FakeTelegramRawPollingClient(fetch_queue=[raw])
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        r = await rt.poll_once(correlation_id=new_correlation_id())
        assert r.send_count == 1
        assert r.fetch_failure_count == 0
        assert r.raw_received_count == 1
        assert len(client.send_calls) == 1

    _run(main())


def test_duplicate_start_same_update_id_batch_one_send_one_audit() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        raw = _update(update_id=5, message=_base_message(user_id=42, text="/start"))
        client = FakeTelegramRawPollingClient(fetch_queue=[raw, raw])
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        r = await rt.poll_once(correlation_id=new_correlation_id())
        assert r.send_count == 1
        assert r.noop_count == 1
        assert len(await c.audit.recorded_events()) == 1

    _run(main())


def test_duplicate_start_two_poll_once_one_audit() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        raw = _update(update_id=5, message=_base_message(user_id=42, text="/start"))
        client = FakeTelegramRawPollingClient(fetch_queue=[raw])
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        cid = new_correlation_id()
        r1 = await rt.poll_once(correlation_id=cid)
        r2 = await rt.poll_once(correlation_id=cid)
        assert r1.send_count == 1 and r1.noop_count == 0
        assert r2.send_count == 0 and r2.noop_count == 1
        assert len(await c.audit.recorded_events()) == 1

    _run(main())


def test_mixed_batch_accepted_rejected_bridge_exception() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramRawPollingClient()
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

        async def fetch_raw_updates(*, limit: int, offset: int | None = None):
            return [good, rejected, bad]

        class C:
            async def fetch_raw_updates(self, *, limit: int, offset: int | None = None):
                return await fetch_raw_updates(limit=limit, offset=offset)

            async def send_text_message(
                self, chat_id: int, text: str, *, correlation_id: str, reply_markup: object | None = None,
                parse_mode: str | None = None,
            ) -> int:
                return await client.send_text_message(chat_id, text, correlation_id=correlation_id)

        rt = Slice1RawPollingRuntime(c, C(), bridge)
        r = await rt.poll_once()
        assert r.raw_received_count == 3
        assert r.bridge_accepted_count == 1
        assert r.bridge_rejected_count == 1
        assert r.bridge_exception_count == 1
        assert r.send_count == 1
        assert r.noop_count == 0
        assert client.send_calls[0][0] == 10

    _run(main())


def test_fetch_failure_safe_result() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramRawPollingClient()
        client.set_fetch_raises(RuntimeError("network"))
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        r = await rt.poll_once()
        assert r == RawPollingBatchResult(
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

    _run(main())


def test_correlation_id_reaches_send_path() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramRawPollingClient(
            fetch_queue=[_update(message=_base_message(text="/start"))],
        )
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        cid = new_correlation_id()
        await rt.poll_once(correlation_id=cid)
        assert len(client.send_calls) == 1
        assert client.send_calls[0][2] == cid

    _run(main())


def test_poll_once_uses_process_raw_updates_with_bridge(monkeypatch) -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramRawPollingClient(
            fetch_queue=[_update(message=_base_message(text="/start"))],
        )
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        captured: list[object] = []
        orig = process_raw_updates_with_bridge

        async def spy(runtime, raw_updates, bridge, *, correlation_id=None):
            captured.append(True)
            return await orig(runtime, raw_updates, bridge, correlation_id=correlation_id)

        monkeypatch.setattr(raw_polling_mod, "process_raw_updates_with_bridge", spy)
        await rt.poll_once(correlation_id=new_correlation_id())
        assert captured == [True]

    _run(main())


def test_app_runtime_package_exports_raw_polling_api() -> None:
    import app.runtime as rt

    assert hasattr(rt, "TelegramRawPollingClient")
    assert hasattr(rt, "RawPollingBatchResult")
    assert hasattr(rt, "Slice1RawPollingRuntime")
    assert "TelegramRawPollingClient" in rt.__all__
    assert "RawPollingBatchResult" in rt.__all__
    assert "Slice1RawPollingRuntime" in rt.__all__


def test_raw_polling_module_excludes_forbidden_tokens() -> None:
    src = inspect.getsource(raw_polling_mod)
    lower = src.lower()
    assert "billing" not in lower
    assert "issuance" not in lower
    assert "admin" not in lower
    assert "webhook" not in lower
    assert "sdk" not in lower


def test_fake_raw_client_satisfies_protocol() -> None:
    c = FakeTelegramRawPollingClient()
    assert isinstance(c, TelegramRawPollingClient)


def test_success_maps_bound_counters_and_zero_fetch_failure() -> None:
    async def main() -> None:
        u1 = _update(update_id=1, message=_base_message(text="/start"))
        u2 = _update(update_id=2, message=_base_message(user_id=7, text="/start"))
        c_poll = build_slice1_composition()
        client_poll = FakeTelegramRawPollingClient(fetch_queue=[u1, u2])
        rt_poll = Slice1RawPollingRuntime(c_poll, client_poll, _identity_bridge)
        r = await rt_poll.poll_once()

        c_bound = build_slice1_composition()
        rt_bound = Slice1RawPollingRuntime(
            c_bound,
            FakeTelegramRawPollingClient(fetch_queue=[]),
            _identity_bridge,
        )
        br = await process_raw_updates_with_bridge(rt_bound._inner, [u1, u2], _identity_bridge)
        assert r.fetch_failure_count == 0
        assert r.raw_received_count == br.raw_received_count
        assert r.bridge_accepted_count == br.bridge_accepted_count
        assert r.send_count == br.send_count
        assert r.noop_count == br.noop_count

    _run(main())


def test_first_poll_once_passes_offset_none() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramRawPollingClient(fetch_queue=[_update(message=_base_message(text="/start"))])
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        await rt.poll_once()
        assert client.fetch_offset_history == [None]
        assert client.last_fetch_offset is None

    _run(main())


def test_offset_advances_to_max_update_id_plus_one_and_next_fetch_uses_it() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        u = _update(update_id=7, message=_base_message(text="/start"))
        client = FakeTelegramRawPollingClient(
            fetch_rounds=[
                [u],
                [u],
            ],
        )
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        await rt.poll_once()
        assert rt.current_offset == 8
        await rt.poll_once()
        assert client.fetch_offset_history == [None, 8]

    _run(main())


def test_empty_batch_leaves_offset_unchanged() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        u = _update(update_id=3, message=_base_message(text="/start"))
        client = FakeTelegramRawPollingClient(fetch_rounds=[[u], []])
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        await rt.poll_once()
        assert rt.current_offset == 4
        await rt.poll_once()
        assert rt.current_offset == 4
        assert client.fetch_offset_history[-1] == 4

    _run(main())


def test_invalid_update_ids_do_not_advance_offset() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        bad = _update(update_id=1)
        bad.pop("update_id")
        bad["message"] = _base_message(text="/start")
        client = FakeTelegramRawPollingClient(
            fetch_rounds=[
                [bad, {"update_id": "nope", "message": _base_message(text="/start")}],
                [_update(update_id=9, message=_base_message(text="/start"))],
            ],
        )
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        await rt.poll_once()
        assert rt.current_offset is None
        await rt.poll_once()
        assert client.fetch_offset_history == [None, None]
        assert rt.current_offset == 10

    _run(main())


def test_bool_update_id_does_not_advance_offset() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramRawPollingClient(
            fetch_rounds=[
                [{"update_id": True, "message": _base_message(text="/start")}],
                [_update(update_id=2, message=_base_message(text="/start"))],
            ],
        )
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        await rt.poll_once()
        assert rt.current_offset is None
        await rt.poll_once()
        assert rt.current_offset == 3

    _run(main())


def test_smaller_update_ids_do_not_decrease_offset() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        hi = _update(update_id=100, message=_base_message(text="/start"))
        lo = _update(update_id=1, message=_base_message(user_id=2, text="/start"))
        client = FakeTelegramRawPollingClient(fetch_rounds=[[hi], [lo]])
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        await rt.poll_once()
        assert rt.current_offset == 101
        await rt.poll_once()
        assert rt.current_offset == 101
        assert client.fetch_offset_history == [None, 101]

    _run(main())


def test_fetch_exception_does_not_change_offset() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        u = _update(update_id=5, message=_base_message(text="/start"))
        client = FakeTelegramRawPollingClient(fetch_rounds=[[u], []])
        rt = Slice1RawPollingRuntime(c, client, _identity_bridge)
        await rt.poll_once()
        assert rt.current_offset == 6
        client.set_fetch_raises(RuntimeError("boom"))
        r = await rt.poll_once()
        assert r.fetch_failure_count == 1
        assert rt.current_offset == 6

    _run(main())
