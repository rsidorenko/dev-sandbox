"""Очередь оплаты имеет собственный цикл, независимый от часового reconcile."""

import asyncio
from types import SimpleNamespace

from app.runtime.server_sync_scheduler import ServerSyncScheduler


async def test_access_worker_runs_while_reconcile_is_waiting(monkeypatch):
    entered, released = asyncio.Event(), asyncio.Event()
    calls = []

    async def reconcile():
        entered.set()
        await released.wait()
        return 0, 0, 0

    scheduler = ServerSyncScheduler(vless_provider=SimpleNamespace(reconcile_all_users=reconcile), pool=object())

    async def recover(pool, provider):
        await entered.wait()
        calls.append("recovered")
        scheduler.stop()
        released.set()
        return 1

    monkeypatch.setattr("app.runtime.server_sync_scheduler.recover_access_jobs", recover)
    await asyncio.wait_for(scheduler.run(), timeout=3)
    assert calls == ["recovered"]
