"""Tests for :mod:`app.runtime.raw_runner` (thin loop over raw ``poll_once``)."""

from __future__ import annotations

import asyncio
import inspect
from typing import cast

import pytest

from app.runtime.raw_polling import RawPollingBatchResult, Slice1RawPollingRuntime
from app.runtime.raw_runner import Slice1RawPollingRunner, run_raw_polling_iterations
from app.runtime.runner import PollingRunSummary


def _run(coro):
    return asyncio.run(coro)


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
    """Minimal double with async ``poll_once`` (structurally compatible at runtime)."""

    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    async def poll_once(self, *, correlation_id: str | None = None) -> RawPollingBatchResult:
        self.calls.append(correlation_id)
        return _raw_batch()


def _spy_as_runtime(spy: SpyRawRuntime) -> Slice1RawPollingRuntime:
    return cast("Slice1RawPollingRuntime", spy)


def test_zero_iterations_poll_once_not_called() -> None:
    async def main() -> None:
        spy = SpyRawRuntime()
        s = await Slice1RawPollingRunner(_spy_as_runtime(spy)).run_iterations(0)
        assert spy.calls == []
        assert s == PollingRunSummary(
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

    _run(main())


def test_one_iteration_aggregates_raw_result() -> None:
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
        s = await Slice1RawPollingRunner(_spy_as_runtime(spy)).run_iterations(1)
        assert s.iterations_requested == 1
        assert s.iterations_completed == 1
        assert s.received_count == 2
        assert s.send_count == 1
        assert s.noop_count == 1
        assert s.poll_once_exception_count == 0
        assert len(spy.calls) == 1

    _run(main())


def test_multiple_iterations_sum_counters() -> None:
    a = _raw_batch(raw_received_count=1, noop_count=1)
    b = _raw_batch(send_count=1, fetch_failure_count=1)

    async def main() -> None:
        class R(SpyRawRuntime):
            def __init__(self) -> None:
                super().__init__()
                self._seq = iter([a, b, a])

            async def poll_once(self, *, correlation_id: str | None = None) -> RawPollingBatchResult:
                self.calls.append(correlation_id)
                return next(self._seq)

        spy = R()
        s = await Slice1RawPollingRunner(_spy_as_runtime(spy)).run_iterations(3)
        assert s.iterations_requested == 3
        assert s.iterations_completed == 3
        assert s.received_count == 2
        assert s.send_count == 1
        assert s.noop_count == 2
        assert s.fetch_failure_count == 1
        assert len(spy.calls) == 3

    _run(main())


def test_poll_once_exception_counted_loop_continues() -> None:
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
        s = await Slice1RawPollingRunner(_spy_as_runtime(spy)).run_iterations(2)
        assert s.iterations_requested == 2
        assert s.iterations_completed == 1
        assert s.poll_once_exception_count == 1
        assert s.send_count == 1
        assert len(spy.calls) == 2

    _run(main())


def test_mixed_success_exception_success() -> None:
    first = _raw_batch(raw_received_count=1)
    third = _raw_batch(send_count=2, noop_count=1)

    async def main() -> None:
        class R(SpyRawRuntime):
            def __init__(self) -> None:
                super().__init__()
                self._step = 0

            async def poll_once(self, *, correlation_id: str | None = None) -> RawPollingBatchResult:
                self.calls.append(correlation_id)
                self._step += 1
                if self._step == 1:
                    return first
                if self._step == 2:
                    raise ValueError("mid")
                return third

        spy = R()
        s = await Slice1RawPollingRunner(_spy_as_runtime(spy)).run_iterations(3)
        assert s.iterations_requested == 3
        assert s.iterations_completed == 2
        assert s.poll_once_exception_count == 1
        assert s.received_count == 1
        assert s.send_count == 2
        assert s.noop_count == 1

    _run(main())


def test_correlation_id_passed_to_each_poll_once() -> None:
    async def main() -> None:
        spy = SpyRawRuntime()
        await Slice1RawPollingRunner(_spy_as_runtime(spy)).run_iterations(3, correlation_id="cid-1")
        assert spy.calls == ["cid-1", "cid-1", "cid-1"]

    _run(main())


def test_run_raw_polling_iterations_delegates() -> None:
    one = _raw_batch(raw_received_count=3)

    async def main() -> None:
        class R(SpyRawRuntime):
            async def poll_once(self, *, correlation_id: str | None = None) -> RawPollingBatchResult:
                self.calls.append(correlation_id)
                return one

        spy = R()
        s = await run_raw_polling_iterations(_spy_as_runtime(spy), 1)
        assert s.received_count == 3
        assert s.iterations_completed == 1
        assert len(spy.calls) == 1

    _run(main())


def test_runtime_package_exports_raw_runner() -> None:
    from app import runtime as rt

    assert rt.Slice1RawPollingRunner is Slice1RawPollingRunner
    assert rt.run_raw_polling_iterations is run_raw_polling_iterations


def test_raw_runner_module_excludes_forbidden_terms() -> None:
    import app.runtime.raw_runner as mod

    src = inspect.getsource(mod)
    lower = src.lower()
    assert "billing" not in lower
    assert "issuance" not in lower
    assert "admin" not in lower
    assert "webhook" not in lower


def test_negative_iterations_raises() -> None:
    async def main() -> None:
        spy = SpyRawRuntime()
        with pytest.raises(ValueError, match="non-negative"):
            await Slice1RawPollingRunner(_spy_as_runtime(spy)).run_iterations(-1)

    _run(main())


def test_non_int_iterations_raises() -> None:
    async def main() -> None:
        spy = SpyRawRuntime()
        with pytest.raises(TypeError, match="iterations must be int"):
            await Slice1RawPollingRunner(_spy_as_runtime(spy)).run_iterations("3")  # type: ignore[arg-type]

    _run(main())
