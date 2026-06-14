"""Periodic background scheduler that syncs VLESS users across all active VPN servers.

Ensures every non-deleted user (active subscriber + expired-in-grace) has VLESS
keys on every active 3x-ui panel. Only adds missing clients — never modifies or
deletes existing ones. Runs hourly, safe to overlap or run concurrently.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

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

    def __init__(self, *, vless_provider: XuiVlessProvider) -> None:
        self._provider = vless_provider
        self._running = False

    async def run(self) -> None:
        """Main loop: run sync every hour until stopped."""
        self._running = True
        _LOGGER.info("server_sync_scheduler_started")
        while self._running:
            try:
                added, failed, total = await self._provider.reconcile_all_users()
                if added > 0 or failed > 0:
                    _LOGGER.info(
                        "server_sync_completed added=%d failed=%d total=%d",
                        added, failed, total,
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
