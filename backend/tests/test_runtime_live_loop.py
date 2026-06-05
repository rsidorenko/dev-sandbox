"""Tests for :mod:`app.runtime.live_loop`."""

from __future__ import annotations

import inspect
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.application.bootstrap import build_slice1_composition
from app.runtime.live_loop import (
    LoopControl,
    Slice1LivePollingLoop,
    Slice1LiveRawPollingLoop,
    run_live_polling_until_stopped,
    run_live_raw_polling_until_stopped,
)
from app.runtime.polling import PollingBatchResult, Slice1PollingRuntime
from app.runtime.raw_polling import RawPollingBatchResult, Slice1RawPollingRuntime
from app.runtime.runner import PollingRunSummary
from app.shared.test_helpers import run_async as _run


def _zero_batch() -> PollingBatchResult:
    return PollingBatchResult(
        received_count=0,
        send_count=0,
        noop_count=0,
        send_failure_count=0,
        processing_failure_count=0,
        fetch_failure_count=0,
    )


class FakeTelegramPollingClient:
    __slots__ = ("fetch_calls",)

    def __init__(self) -> None:
        self.fetch_calls = 0

    async def fetch_updates(self, *, limit: int):
        self.fetch_calls += 1
        return []

    async def send_text_message(
        self,
        chat_id: int,
        text: str,
        *,
        correlation_id: str,
        disable_web_page_preview: bool = False,
    ) -> int:
        return 1

    async def send_video(
        self,
        chat_id: int,
        video_path: str,
        *,
        correlation_id: str,
        caption: str | None = None,
        reply_markup=None,
        parse_mode: str | None = None,
    ) -> int:
        return 1

    async def answer_callback_query(self, callback_query_id: str) -> None:
        pass

    async def delete_message(self, chat_id: int, message_id: int) -> None:
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


def _raw_batch(**overrides: int) -> RawPollingBatchResult:
    base = {
        "raw_received_count": 0,
        "bridge_accepted_count": 0,
        "bridge_rejected_count": 0,
        "bridge_exception_count": 0,
        "send_count": 0,
        "noop_count": 0,
        "send_failure_count": 0,
        "processing_failure_count": 0,
        "fetch_failure_count": 0,
    }
    base.update(overrides)
    return RawPollingBatchResult(**base)


class SpyRawRuntime:
    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    async def poll_once(self, *, correlation_id: str | None = None) -> RawPollingBatchResult:
        self.calls.append(correlation_id)
        return _raw_batch()


def _spy_as_raw_runtime(spy: SpyRawRuntime) -> Slice1RawPollingRuntime:
    return cast("Slice1RawPollingRuntime", spy)


def test_stop_requested_before_start_no_poll_once_polling(monkeypatch) -> None:
    async def main() -> None:
        called: list[object] = []

        async def spy(*args, **kwargs):
            called.append(True)
            return _zero_batch()

        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", spy)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        ctl = LoopControl(stop_requested=True)
        s = await Slice1LivePollingLoop(rt).run_until_stopped(ctl)
        assert called == []
        assert s == _empty_expected_summary()

    _run(main())


def _empty_expected_summary() -> PollingRunSummary:
    return PollingRunSummary(
        iterations_requested=0,
        iterations_completed=0,
        received_count=0,
        send_count=0,
        noop_count=0,
        send_failure_count=0,
        processing_failure_count=0,
        fetch_failure_count=0,
        poll_once_exception_count=0,
    )


def test_max_iterations_zero_no_poll_once_polling(monkeypatch) -> None:
    async def main() -> None:
        called: list[object] = []

        async def spy(*args, **kwargs):
            called.append(True)
            return _zero_batch()

        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", spy)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        s = await Slice1LivePollingLoop(rt).run_until_stopped(
            LoopControl(),
            max_iterations=0,
        )
        assert called == []
        assert s == _empty_expected_summary()

    _run(main())


def test_one_successful_poll_once_aggregated_polling(monkeypatch) -> None:
    one = PollingBatchResult(
        received_count=2,
        send_count=1,
        noop_count=1,
        send_failure_count=0,
        processing_failure_count=0,
        fetch_failure_count=0,
    )

    async def main() -> None:
        mock_po = AsyncMock(return_value=one)
        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", mock_po)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        s = await Slice1LivePollingLoop(rt).run_until_stopped(
            LoopControl(),
            max_iterations=1,
        )
        assert s.iterations_requested == 1
        assert s.iterations_completed == 1
        assert s.received_count == 2
        assert s.send_count == 1
        assert s.noop_count == 1
        assert s.poll_once_exception_count == 0
        mock_po.assert_awaited_once()

    _run(main())


def test_multiple_successful_iterations_sum_polling(monkeypatch) -> None:
    a = PollingBatchResult(1, 0, 1, 0, 0, 0)
    b = PollingBatchResult(0, 1, 0, 0, 0, 1)

    async def main() -> None:
        mock_po = AsyncMock(side_effect=[a, b, a])
        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", mock_po)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        s = await Slice1LivePollingLoop(rt).run_until_stopped(
            LoopControl(),
            max_iterations=3,
        )
        assert s.iterations_requested == 3
        assert s.iterations_completed == 3
        assert s.received_count == 2
        assert s.send_count == 1
        assert s.noop_count == 2
        assert s.fetch_failure_count == 1

    _run(main())


def test_poll_once_exception_counted_loop_continues_polling(monkeypatch) -> None:
    ok = PollingBatchResult(1, 1, 0, 0, 0, 0)

    async def main() -> None:
        async def boom_then_ok(*args, **kwargs):
            if boom_then_ok.n == 0:
                boom_then_ok.n += 1
                raise RuntimeError("unexpected inside poll_once")
            return ok

        boom_then_ok.n = 0
        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", boom_then_ok)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        s = await Slice1LivePollingLoop(rt).run_until_stopped(
            LoopControl(),
            max_iterations=2,
        )
        assert s.iterations_requested == 2
        assert s.iterations_completed == 1
        assert s.iterations_completed + s.poll_once_exception_count == s.iterations_requested
        assert s.poll_once_exception_count == 1
        assert s.send_count == 1

    _run(main())


def test_correlation_id_passed_each_poll_once_polling(monkeypatch) -> None:
    async def main() -> None:
        mock_po = AsyncMock(return_value=_zero_batch())
        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", mock_po)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        await Slice1LivePollingLoop(rt).run_until_stopped(
            LoopControl(),
            correlation_id="cid-live",
            max_iterations=3,
        )
        for call in mock_po.await_args_list:
            assert call.kwargs.get("correlation_id") == "cid-live"

    _run(main())


def test_max_iterations_bounds_loop_polling(monkeypatch) -> None:
    async def main() -> None:
        mock_po = AsyncMock(return_value=_zero_batch())
        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", mock_po)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        await Slice1LivePollingLoop(rt).run_until_stopped(
            LoopControl(),
            max_iterations=2,
        )
        assert mock_po.await_count == 2

    _run(main())


def test_stop_via_control_after_ticks_polling(monkeypatch) -> None:
    async def main() -> None:
        ctl = LoopControl()

        async def tick(*args, **kwargs):
            if tick.n == 2:
                ctl.stop_requested = True
            tick.n += 1
            return _zero_batch()

        tick.n = 0
        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", tick)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        s = await Slice1LivePollingLoop(rt).run_until_stopped(ctl, max_iterations=None)
        assert tick.n == 3
        assert s.iterations_requested == 3
        assert s.iterations_completed == 3

    _run(main())


def test_raw_stop_before_start_no_poll_once() -> None:
    async def main() -> None:
        spy = SpyRawRuntime()
        ctl = LoopControl(stop_requested=True)
        s = await Slice1LiveRawPollingLoop(_spy_as_raw_runtime(spy)).run_until_stopped(ctl)
        assert spy.calls == []
        assert s == _empty_expected_summary()

    _run(main())


def test_raw_max_iterations_zero() -> None:
    async def main() -> None:
        spy = SpyRawRuntime()
        s = await Slice1LiveRawPollingLoop(_spy_as_raw_runtime(spy)).run_until_stopped(
            LoopControl(),
            max_iterations=0,
        )
        assert spy.calls == []
        assert s == _empty_expected_summary()

    _run(main())


def test_raw_one_iteration_aggregates() -> None:
    one = _raw_batch(
        raw_received_count=2,
        send_count=1,
        noop_count=1,
        bridge_accepted_count=1,
    )

    async def main() -> None:
        class R(SpyRawRuntime):
            async def poll_once(self, *, correlation_id: str | None = None) -> RawPollingBatchResult:
                self.calls.append(correlation_id)
                return one

        spy = R()
        s = await Slice1LiveRawPollingLoop(_spy_as_raw_runtime(spy)).run_until_stopped(
            LoopControl(),
            max_iterations=1,
        )
        assert s.iterations_requested == 1
        assert s.received_count == 2
        assert s.send_count == 1

    _run(main())


def test_raw_exception_then_continue() -> None:
    ok = _raw_batch(raw_received_count=1, send_count=1)

    async def main() -> None:
        class R(SpyRawRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.n = 0

            async def poll_once(self, *, correlation_id: str | None = None) -> RawPollingBatchResult:
                self.calls.append(correlation_id)
                if self.n == 0:
                    self.n += 1
                    raise RuntimeError("boom")
                return ok

        spy = R()
        s = await Slice1LiveRawPollingLoop(_spy_as_raw_runtime(spy)).run_until_stopped(
            LoopControl(),
            max_iterations=2,
        )
        assert s.poll_once_exception_count == 1
        assert s.iterations_completed == 1
        assert s.iterations_requested == s.iterations_completed + s.poll_once_exception_count

    _run(main())


def test_raw_correlation_id_and_max_iterations() -> None:
    async def main() -> None:
        spy = SpyRawRuntime()
        await Slice1LiveRawPollingLoop(_spy_as_raw_runtime(spy)).run_until_stopped(
            LoopControl(),
            correlation_id="r1",
            max_iterations=2,
        )
        assert spy.calls == ["r1", "r1"]

    _run(main())


def test_run_live_polling_until_stopped_delegates(monkeypatch) -> None:
    async def main() -> None:
        one = PollingBatchResult(3, 0, 0, 0, 0, 0)
        mock_po = AsyncMock(return_value=one)
        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", mock_po)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        s = await run_live_polling_until_stopped(
            rt,
            LoopControl(),
            max_iterations=1,
        )
        assert s.received_count == 3

    _run(main())


def test_run_live_raw_polling_until_stopped_delegates() -> None:
    async def main() -> None:
        class R(SpyRawRuntime):
            async def poll_once(self, *, correlation_id: str | None = None) -> RawPollingBatchResult:
                self.calls.append(correlation_id)
                return _raw_batch(raw_received_count=3)

        spy = R()
        s = await run_live_raw_polling_until_stopped(
            _spy_as_raw_runtime(spy),
            LoopControl(),
            max_iterations=1,
        )
        assert s.received_count == 3

    _run(main())


def test_runtime_package_exports_live_loop() -> None:
    from app import runtime as rt

    assert rt.LoopControl is LoopControl
    assert rt.Slice1LivePollingLoop is Slice1LivePollingLoop
    assert rt.Slice1LiveRawPollingLoop is Slice1LiveRawPollingLoop
    assert rt.run_live_polling_until_stopped is run_live_polling_until_stopped
    assert rt.run_live_raw_polling_until_stopped is run_live_raw_polling_until_stopped


def test_live_loop_module_forbidden_substrings() -> None:
    import app.runtime.live_loop as mod

    src = inspect.getsource(mod)
    lower = src.lower()
    for term in ("billing", "issuance", "admin", "webhook", "signal", "sleep"):
        assert term not in lower


def test_negative_max_iterations_raises_polling() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        with pytest.raises(ValueError, match="non-negative"):
            await Slice1LivePollingLoop(rt).run_until_stopped(
                LoopControl(),
                max_iterations=-1,
            )

    _run(main())


def test_non_int_max_iterations_raises_polling() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        with pytest.raises(TypeError, match="max_iterations must be int"):
            await Slice1LivePollingLoop(rt).run_until_stopped(
                LoopControl(),
                max_iterations=cast("int", "2"),
            )

    _run(main())
