"""Сериализация сетевых изменений с bulk-reconcile между процессами."""

import asyncio
from contextlib import asynccontextmanager
from functools import wraps

import asyncpg

_LOCK_ID = 728390124522


@asynccontextmanager
async def vpn_fleet_lock(pool):
    while True:
        connection = await pool.acquire()
        try:
            locked = await connection.fetchval("SELECT pg_try_advisory_lock($1)", _LOCK_ID)
        except BaseException:
            await pool.release(connection)
            raise
        if locked:
            break
        await pool.release(connection)
        await asyncio.sleep(0.1)
    try:
        yield
    finally:
        try:
            await connection.execute("SELECT pg_advisory_unlock($1)", _LOCK_ID)
        finally:
            await pool.release(connection)


def serialized_mutation(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        task = asyncio.current_task()
        if self._mutation_owner is task:
            return await method(self, *args, **kwargs)
        async with self._mutation_lock:
            self._mutation_owner = task
            try:
                if isinstance(self._pool, asyncpg.Pool):
                    async with vpn_fleet_lock(self._pool):
                        return await method(self, *args, **kwargs)
                return await method(self, *args, **kwargs)
            finally:
                self._mutation_owner = None

    return wrapped
