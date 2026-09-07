"""Фоновый сбор per-user трафика bare_xray хостов (StatsService/QueryStats).

Каждый тик (``TRAFFIC_STATS_INTERVAL_SECONDS``, default 300с): по одному опросу
на хост (группировка (server_host, api_port) — счётчики per-PROCESS, опрос per
инбаунд считал бы хост дважды), дифф относительно сохранённых абсолютов
(``diff_deltas``: clamp-to-new после рестарта xray), аккумуляция дельт в
``bare_xray_traffic_daily`` + новые абсолюты в ``bare_xray_traffic_state``
(одной транзакцией — см. postgres_traffic_stats.apply_batch).

Сбой одного хоста изолирован: остальные хосты тик не срывают. advisory-lock
20260901 — два рантайма (polling и webhook) не дублируют сбор. Инертен до
флага ``TRAFFIC_STATS_ENABLE`` (default OFF) И до включения StatsService на
хостах (rollout-скрипт scripts/enable_bare_xray_stats.py): без него QueryStats
падает GRPC_FAIL и хост просто пропускается с warning-логом.
"""

from __future__ import annotations

import asyncio
import logging
import os

import asyncpg

from app.issuance.bare_xray_traffic import build_batch, traffic_stats_enabled_from_env
from app.issuance.xui_vless_provider import XuiVlessProvider
from app.persistence.postgres_traffic_stats import PostgresTrafficStatsRepository

_LOGGER = logging.getLogger(__name__)

_COLLECT_INTERVAL_SECONDS = int(os.environ.get("TRAFFIC_STATS_INTERVAL_SECONDS", "300"))
_ADVISED_LOCK_ID = 20260901


class TrafficStatsScheduler:
    """Периодический опрос QueryStats → bare_xray_traffic_daily/state."""

    def __init__(self, *, pool: asyncpg.Pool, vless_provider: XuiVlessProvider) -> None:
        self._pool = pool
        self._provider = vless_provider
        self._repo = PostgresTrafficStatsRepository(pool)
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._run_tick()
            except Exception:
                _LOGGER.exception("traffic_stats_tick_error")
            await self._sleep(_COLLECT_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False

    async def _sleep(self, seconds: int) -> None:
        for _ in range(seconds):
            if not self._running:
                return
            await asyncio.sleep(1)

    async def _run_tick(self) -> None:
        async with self._pool.acquire() as conn:
            locked = await conn.fetchval("SELECT pg_try_advisory_lock($1)", _ADVISED_LOCK_ID)
            if not locked:
                _LOGGER.info("traffic_stats tick skipped — another instance holds the lock")
                return
            try:
                await self._collect_and_apply()
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISED_LOCK_ID)

    async def _collect_and_apply(self) -> None:
        hosts = await self._provider.bare_xray_stats_hosts()
        if not hosts:
            return
        host_results = []
        for client, configs in hosts:
            try:
                current = await client.query_stats()
            except Exception as exc:
                _LOGGER.warning("traffic_stats query error server=%s: %r", client.server_id, exc)
                continue
            if current is None:
                # Транспортный сбой или StatsService ещё не включён на хосте.
                _LOGGER.warning("traffic_stats query failed server=%s — host skipped", client.server_id)
                continue
            try:
                state: dict[str, dict[str, int]] = {}
                for cfg in configs:
                    # state грузим по всем строкам хоста: email↔server_id связан
                    # префиксом транспорта, дифф идёт по email в рамках хоста.
                    state.update(await self._repo.load_state(cfg.server_id))
            except Exception as exc:
                # Неизвестный state = небезопасный дифф (риск двойного подсчёта) —
                # пропускаем ВЕСЬ хост, следующий тик повторит с полным state.
                _LOGGER.warning("traffic_stats state load failed server=%s: %r", client.server_id, exc)
                continue
            host_results.append((configs, current, state))
        batch = build_batch(host_results)
        await self._repo.apply_batch(batch)
        if batch:
            _LOGGER.info("traffic_stats tick ok hosts=%s samples=%s", len(host_results), len(batch))


def start_traffic_stats_scheduler(*, pool: asyncpg.Pool, vless_provider: XuiVlessProvider) -> TrafficStatsScheduler:
    return TrafficStatsScheduler(pool=pool, vless_provider=vless_provider)


__all__ = [
    "TrafficStatsScheduler",
    "start_traffic_stats_scheduler",
    "traffic_stats_enabled_from_env",
]
