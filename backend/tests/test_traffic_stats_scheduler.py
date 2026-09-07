"""Тесты таймаут-защиты TrafficStatsScheduler (инцидент 2026-09-07).

Зависшая SSH-нога (handshake без banner/auth таймаутов / канал без exit-status)
клинила тик навсегда, держа advisory-lock 20260901. Требования после фикса:
  * один зависший хост срезается _HOST_QUERY_TIMEOUT_SECONDS и не срывает тик;
  * даже полностью зависший тик срезается _TICK_HARD_TIMEOUT_SECONDS, а лок
    ВСЕГДА освобождается (finally под wait_for)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from app.runtime import traffic_stats_scheduler as tss


class _FakeConn:
    def __init__(self) -> None:
        self.executed: list[str] = []

    async def fetchval(self, sql: str, *args: Any) -> int:
        return 1  # pg_try_advisory_lock -> захвачен

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append(sql)
        return "OK"

    def transaction(self):
        @contextlib.asynccontextmanager
        async def _tx():
            yield

        return _tx()


class _FakePool:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def acquire(self):
        @contextlib.asynccontextmanager
        async def _acq():
            yield self.conn

        return _acq()


class _HungClient:
    server_id = 2

    async def query_stats(self) -> dict[str, dict[str, int]]:
        await asyncio.sleep(30)
        return {}  # pragma: no cover - не дойдём


class _HungProvider:
    def __init__(self, hosts: list[tuple[Any, tuple[Any, ...]]] | None = None, *, delay: float = 0.0) -> None:
        self._hosts = hosts
        self._delay = delay

    async def bare_xray_stats_hosts(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._hosts


def _scheduler(provider) -> tuple[tss.TrafficStatsScheduler, _FakePool]:
    pool = _FakePool()
    return tss.TrafficStatsScheduler(pool=pool, vless_provider=provider), pool


def test_hung_host_is_cut_and_tick_completes(monkeypatch, caplog) -> None:
    """query_stats зависает -> хост срезан warning'ом, тик ДОЖИЛ до unlock."""
    monkeypatch.setattr(tss, "_HOST_QUERY_TIMEOUT_SECONDS", 0.05)
    sched, pool = _scheduler(_HungProvider([(_HungClient(), ())]))

    async def _run() -> None:
        await sched._run_tick()

    asyncio.run(asyncio.wait_for(_run(), timeout=5))
    assert any("query error" in r.message for r in caplog.records)
    # лок освобождён
    assert any("pg_advisory_unlock" in sql for sql in pool.conn.executed)


def test_fully_hung_tick_releases_lock(monkeypatch) -> None:
    """Даже зависший целиком тик (bare_xray_stats_hosts не отвечает) срезается
    wait_for, а finally освобождает advisory-lock."""
    monkeypatch.setattr(tss, "_TICK_HARD_TIMEOUT_SECONDS", 0.05)
    sched, pool = _scheduler(_HungProvider(delay=30))

    async def _run() -> None:
        await sched._run_tick()

    # TimeoutError выходит наружу (run()-цикл ловит и логирует), но лок уже свободен
    try:
        asyncio.run(asyncio.wait_for(_run(), timeout=5))
        raised = False
    except TimeoutError:
        raised = True
    assert raised
    assert any("pg_advisory_unlock" in sql for sql in pool.conn.executed)


def test_run_loop_survives_tick_exception(monkeypatch) -> None:
    """run() ловит исключение тика и продолжает цикл (не умирает молча)."""
    monkeypatch.setattr(tss, "_TICK_HARD_TIMEOUT_SECONDS", 0.05)
    # интервал обязан быть >=1: _sleep(0) не содержит await -> таск не отдаёт
    # event loop и морит голодом остальные корутины (звёздный готч этого теста).
    monkeypatch.setattr(tss, "_COLLECT_INTERVAL_SECONDS", 1)

    calls = {"n": 0}

    class _FlakyProvider(_HungProvider):
        async def bare_xray_stats_hosts(self):
            calls["n"] += 1
            if calls["n"] == 1:
                await asyncio.sleep(30)  # первый тик зависает под wait_for
            return []

    sched, pool = _scheduler(_FlakyProvider())

    async def _run_for_a_while() -> None:
        task = asyncio.create_task(sched.run())
        await asyncio.sleep(2.5)
        sched.stop()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(_run_for_a_while())
    # первый тик упал по таймауту, второй дошёл до hosts=[] — цикл жив
    assert calls["n"] >= 2
