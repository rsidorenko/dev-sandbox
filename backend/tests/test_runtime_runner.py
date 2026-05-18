"""Tests for :mod:`app.runtime.runner` (thin loop over ``poll_once``)."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

from app.application.bootstrap import build_slice1_composition
from app.runtime.polling import PollingBatchResult, Slice1PollingRuntime
from app.runtime.runner import PollingRunSummary, Slice1PollingRunner, run_polling_iterations
from app.shared.test_helpers import run_async as _run


class FakeTelegramPollingClient:
    """In-memory double (same role as in ``test_runtime_polling``)."""

    __slots__ = ("fetch_calls", "last_fetch_limit", "send_calls", "send_fail")

    def __init__(self) -> None:
        self.fetch_calls = 0
        self.last_fetch_limit: int | None = None
        self.send_calls: list[tuple[int, str, str]] = []
        self.send_fail = False

    async def fetch_updates(self, *, limit: int):
        self.fetch_calls += 1
        self.last_fetch_limit = limit
        return []

    async def send_text_message(
        self,
        chat_id: int,
        text: str,
        *,
        correlation_id: str,
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


def _zero_batch() -> PollingBatchResult:
    return PollingBatchResult(
        received_count=0,
        send_count=0,
        noop_count=0,
        send_failure_count=0,
        processing_failure_count=0,
        fetch_failure_count=0,
    )


def test_zero_iterations_poll_once_not_called(monkeypatch) -> None:
    async def main() -> None:
        called: list[object] = []

        async def spy(*args, **kwargs):
            called.append(True)
            return _zero_batch()

        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", spy)
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        rt = Slice1PollingRuntime(c, client)
        runner = Slice1PollingRunner(rt)
        s = await runner.run_iterations(0)
        assert called == []
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


def test_one_iteration_aggregates_one_result(monkeypatch) -> None:
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
        s = await Slice1PollingRunner(rt).run_iterations(1)
        assert s.iterations_requested == 1
        assert s.iterations_completed == 1
        assert s.received_count == 2
        assert s.send_count == 1
        assert s.noop_count == 1
        assert s.poll_once_exception_count == 0
        mock_po.assert_awaited_once()

    _run(main())


def test_multiple_iterations_sums_counters(monkeypatch) -> None:
    a = PollingBatchResult(1, 0, 1, 0, 0, 0)
    b = PollingBatchResult(0, 1, 0, 0, 0, 1)

    async def main() -> None:
        mock_po = AsyncMock(side_effect=[a, b, a])
        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", mock_po)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        s = await Slice1PollingRunner(rt).run_iterations(3)
        assert s.iterations_requested == 3
        assert s.iterations_completed == 3
        assert s.received_count == 2
        assert s.send_count == 1
        assert s.noop_count == 2
        assert s.fetch_failure_count == 1
        assert mock_po.await_count == 3

    _run(main())


def test_mixed_safe_outcomes_across_iterations(monkeypatch) -> None:
    fetch_fail = PollingBatchResult(0, 0, 0, 0, 0, 1)
    send_fail = PollingBatchResult(1, 0, 0, 1, 0, 0)
    ok = PollingBatchResult(1, 1, 0, 0, 0, 0)

    async def main() -> None:
        mock_po = AsyncMock(side_effect=[fetch_fail, send_fail, ok])
        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", mock_po)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        s = await Slice1PollingRunner(rt).run_iterations(3)
        assert s.iterations_completed == 3
        assert s.fetch_failure_count == 1
        assert s.send_failure_count == 1
        assert s.send_count == 1
        assert s.received_count == 2

    _run(main())


def test_poll_once_exception_aggregated_runner_continues(monkeypatch) -> None:
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
        s = await Slice1PollingRunner(rt).run_iterations(2)
        assert s.iterations_requested == 2
        assert s.iterations_completed == 1
        assert s.poll_once_exception_count == 1
        assert s.send_count == 1

    _run(main())


def test_runner_uses_poll_once_not_client(monkeypatch) -> None:
    async def main() -> None:
        mock_po = AsyncMock(return_value=_zero_batch())
        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", mock_po)
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        rt = Slice1PollingRuntime(c, client)
        await Slice1PollingRunner(rt).run_iterations(2)
        assert mock_po.await_count == 2
        assert client.fetch_calls == 0

    _run(main())


def test_run_polling_iterations_delegates_to_runner(monkeypatch) -> None:
    async def main() -> None:
        one = PollingBatchResult(3, 0, 0, 0, 0, 0)
        mock_po = AsyncMock(return_value=one)
        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", mock_po)
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        s = await run_polling_iterations(rt, 1)
        assert s.received_count == 3
        assert s.iterations_completed == 1

    _run(main())


def test_negative_iterations_raises() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        runner = Slice1PollingRunner(rt)
        try:
            await runner.run_iterations(-1)
        except ValueError:
            return
        raise AssertionError("expected ValueError")

    _run(main())


def test_non_int_iterations_raises() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        rt = Slice1PollingRuntime(c, FakeTelegramPollingClient())
        try:
            await Slice1PollingRunner(rt).run_iterations("3")  # type: ignore[arg-type]
        except TypeError:
            return
        raise AssertionError("expected TypeError")

    _run(main())


def test_runner_module_excludes_billing_issuance_admin_webhook() -> None:
    import app.runtime.runner as runner_mod

    src = inspect.getsource(runner_mod)
    lower = src.lower()
    assert "billing" not in lower
    assert "issuance" not in lower
    assert "admin" not in lower
    assert "webhook" not in lower


def test_runtime_package_reexports_runner() -> None:
    from app import runtime as rt

    assert rt.PollingRunSummary is PollingRunSummary
    assert rt.Slice1PollingRunner is Slice1PollingRunner
    assert rt.run_polling_iterations is run_polling_iterations
