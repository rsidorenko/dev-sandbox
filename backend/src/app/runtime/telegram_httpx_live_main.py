"""Runnable process entrypoint for slice-1 live runtime from env."""

from __future__ import annotations

import asyncio
import logging
import signal

from app.observability.logging_policy import sanitize_structured_fields
from app.runtime.notification_scheduler import NotificationScheduler, start_notification_scheduler
from app.runtime.runner import PollingRunSummary
from app.runtime.telegram_httpx_live_process import (
    Slice1HttpxLiveProcess,
    build_slice1_httpx_live_process_from_env_async,
)

_BOT_MENU_COMMANDS: list[dict[str, str]] = [
    {"command": "start", "description": "Начать / главное меню"},
]

_LOGGER = logging.getLogger(__name__)


def _log_lifecycle_event(*, intent: str, outcome: str, operation: str) -> None:
    _LOGGER.info(
        "runtime.live.entrypoint.lifecycle",
        extra={
            "structured_fields": sanitize_structured_fields(
                {
                    "intent": intent,
                    "outcome": outcome,
                    "operation": operation,
                }
            )
        },
    )


def _make_signal_stop_callback(
    process: Slice1HttpxLiveProcess,
    *,
    signal_name: str,
):
    def _callback() -> None:
        _log_lifecycle_event(
            intent="shutdown_request",
            outcome="signal_received",
            operation=f"signal:{signal_name}",
        )
        process.request_stop()

    return _callback


def _register_signal_stop_handlers(
    process: Slice1HttpxLiveProcess,
) -> None:
    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, signame, None)
        if signum is None:
            continue
        try:
            loop.add_signal_handler(
                signum,
                _make_signal_stop_callback(process, signal_name=signame),
            )
        except (NotImplementedError, RuntimeError):
            # Platform/loop can refuse signal handlers (e.g. Windows).
            continue


async def run_slice1_httpx_live_from_env() -> PollingRunSummary:
    _log_lifecycle_event(
        intent="startup",
        outcome="begin",
        operation="run_until_stopped",
    )
    try:
        process = await build_slice1_httpx_live_process_from_env_async()
    except Exception:
        _LOGGER.error(
            "runtime.live.entrypoint.failed",
            extra={
                "structured_fields": sanitize_structured_fields(
                    {
                        "intent": "startup",
                        "outcome": "error",
                        "operation": "build_process",
                        "internal_category": "startup_exception",
                    }
                )
            },
        )
        raise
    try:
        try:
            _register_signal_stop_handlers(process)
        except (NotImplementedError, RuntimeError):
            # Keep run path functional when signal hooks are unavailable.
            pass
        try:
            await process.app.bundle.client.set_my_commands(_BOT_MENU_COMMANDS)
            _log_lifecycle_event(
                intent="startup",
                outcome="completed",
                operation="set_my_commands",
            )
        except Exception:
            _LOGGER.warning(
                "runtime.live.entrypoint.set_my_commands_failed",
                extra={
                    "structured_fields": sanitize_structured_fields(
                        {
                            "intent": "startup",
                            "outcome": "warning",
                            "operation": "set_my_commands",
                        }
                    )
                },
            )
        # Start notification scheduler if pg_pool and vless_provider are available
        scheduler: NotificationScheduler | None = None
        scheduler_task: asyncio.Task | None = None
        try:
            pool = process.app.bundle.pg_pool
            composition = process.app.bundle.bundle.composition
            if pool is not None and composition.vless_provider is not None:
                scheduler = start_notification_scheduler(
                    pool=pool,
                    bot_token=process.app.bundle.client._bot_token,
                    vless_provider=composition.vless_provider,
                )
                scheduler_task = asyncio.create_task(scheduler.run())
                _log_lifecycle_event(
                    intent="startup",
                    outcome="completed",
                    operation="notification_scheduler_started",
                )
        except Exception:
            _LOGGER.warning("notification_scheduler_start_failed", exc_info=True)

        try:
            summary = await process.run_until_stopped()
        except Exception:
            _LOGGER.error(
                "runtime.live.entrypoint.failed",
                extra={
                    "structured_fields": sanitize_structured_fields(
                        {
                            "intent": "runtime_loop",
                            "outcome": "error",
                            "operation": "run_until_stopped",
                            "internal_category": "runtime_exception",
                        }
                    )
                },
            )
            raise
        _log_lifecycle_event(
            intent="shutdown",
            outcome="completed",
            operation="run_until_stopped",
        )
        return summary
    finally:
        if scheduler is not None:
            scheduler.stop()
        if scheduler_task is not None:
            scheduler_task.cancel()
        await process.aclose()


def main() -> None:
    asyncio.run(run_slice1_httpx_live_from_env())


if __name__ == "__main__":
    main()
