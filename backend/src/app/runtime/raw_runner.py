"""Thin iteration loop over :meth:`Slice1RawPollingRuntime.poll_once`."""

from __future__ import annotations

from app.runtime.raw_polling import Slice1RawPollingRuntime
from app.runtime.runner import PollingRunSummary


class Slice1RawPollingRunner:
    """Runs ``poll_once`` N times and sums :class:`RawPollingBatchResult` counters."""

    __slots__ = ("_runtime",)

    def __init__(self, runtime: Slice1RawPollingRuntime) -> None:
        self._runtime = runtime

    async def run_iterations(
        self,
        iterations: int,
        *,
        correlation_id: str | None = None,
    ) -> PollingRunSummary:
        if type(iterations) is not int:
            raise TypeError("iterations must be int")
        if iterations < 0:
            raise ValueError("iterations must be non-negative")
        if iterations == 0:
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
        received = 0
        send = 0
        noop = 0
        send_fail = 0
        process_fail = 0
        fetch_fail = 0
        completed = 0
        exc_count = 0
        for _ in range(iterations):
            try:
                batch = await self._runtime.poll_once(correlation_id=correlation_id)
            except Exception:
                exc_count += 1
                continue
            completed += 1
            received += batch.raw_received_count
            send += batch.send_count
            noop += batch.noop_count
            send_fail += batch.send_failure_count
            process_fail += batch.processing_failure_count
            fetch_fail += batch.fetch_failure_count
        return PollingRunSummary(
            iterations_requested=iterations,
            iterations_completed=completed,
            received_count=received,
            send_count=send,
            noop_count=noop,
            send_failure_count=send_fail,
            processing_failure_count=process_fail,
            fetch_failure_count=fetch_fail,
            poll_once_exception_count=exc_count,
        )


async def run_raw_polling_iterations(
    runtime: Slice1RawPollingRuntime,
    iterations: int,
    *,
    correlation_id: str | None = None,
) -> PollingRunSummary:
    """Run :meth:`Slice1RawPollingRunner.run_iterations` for a given runtime."""
    return await Slice1RawPollingRunner(runtime).run_iterations(
        iterations,
        correlation_id=correlation_id,
    )
