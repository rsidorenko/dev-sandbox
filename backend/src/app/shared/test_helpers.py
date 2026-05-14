"""Shared test helpers available to all test modules via app.shared.test_helpers."""

from __future__ import annotations

import asyncio


def run_async(coro):
    """Run an async coroutine from sync test code.

    Handles the case where an event loop is already running (e.g. inside
    pytest-asyncio) by delegating to a ThreadPoolExecutor.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)
