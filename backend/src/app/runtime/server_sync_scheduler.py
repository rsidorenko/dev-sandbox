"""Часовая сверка серверов и отдельный цикл восстановления оплаченного доступа."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.application.balance_access_jobs import recover_access_jobs

if TYPE_CHECKING:
    from app.issuance.xui_vless_provider import XuiVlessProvider

_LOGGER = logging.getLogger(__name__)

_SYNC_INTERVAL_SECONDS = 60 * 60  # 1 hour


class ServerSyncScheduler:
    """Periodic reconciliation of VLESS users across VPN servers.

    Reuses ``XuiVlessProvider.reconcile_all_users`` which probes each server and
    adds missing clients (enabled for active, disabled for expired). Non-
    destructive, idempotent, exception-safe.
    """

    def __init__(self, *, vless_provider: XuiVlessProvider, pool=None) -> None:
        self._provider = vless_provider
        self._pool = pool
        self._running = False

    async def run(self) -> None:
        """Запускаем независимые циклы сверки серверов и обработки заданий оплаты."""
        self._running = True
        _LOGGER.info("server_sync_scheduler_started")
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._sync_loop())
            tasks.create_task(self._rotation_loop())
            if self._pool is not None:
                tasks.create_task(self._access_loop())

    async def _rotation_loop(self) -> None:
        """Ротация работает независимо от необязательного сбора статистики."""
        while self._running:
            try:
                await self._provider.process_key_rotations()
            except Exception:
                _LOGGER.exception("key_rotation_worker_error")
            await self._sleep(60)

    async def _access_loop(self) -> None:
        while self._running:
            try:
                await recover_access_jobs(self._pool, self._provider)
            except Exception:
                _LOGGER.exception("balance_access_worker_error")
            await self._sleep(30)

    async def _sync_loop(self) -> None:
        while self._running:
            try:
                added, failed, total = await self._provider.reconcile_all_users()
                if added > 0 or failed > 0:
                    _LOGGER.info(
                        "server_sync_completed added=%d failed=%d total=%d",
                        added,
                        failed,
                        total,
                    )
            except Exception:
                _LOGGER.exception("server_sync_error")
            await self._sleep(_SYNC_INTERVAL_SECONDS)

    def stop(self) -> None:
        """Signal the scheduler to stop after current iteration."""
        self._running = False

    async def _sleep(self, seconds: int) -> None:
        """Interruptible sleep — checks ``_running`` every second."""
        for _ in range(seconds):
            if not self._running:
                return
            await asyncio.sleep(1)
