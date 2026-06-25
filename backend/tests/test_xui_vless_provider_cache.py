"""Focused tests for XuiVlessProvider.get_user_config serve-stale-while-revalidate cache.

The real probe fans out one panel call per active server, so we replace ``_probe_user_config``
with a controllable fake (no real panels / pool needed) and assert the *cache* behaviour:
cold miss probes once, warm reads don't probe, stale reads serve cached + refresh in the
background, and invalidations can't be clobbered by an in-flight refresh.
"""

from __future__ import annotations

import asyncio
import time

from app.issuance.vless_provider import (
    VlessProviderOutcome,
    VlessProviderResult,
    VlessServerConfig,
    VlessUserConfig,
)
from app.issuance.xui_vless_provider import XuiVlessProvider
from app.shared.test_helpers import run_async as _run

_SUCCESS = VlessProviderOutcome.SUCCESS
_NOT_FOUND = VlessProviderOutcome.NOT_FOUND
_UNAVAILABLE = VlessProviderOutcome.UNAVAILABLE


def _cfg(tag: str) -> VlessUserConfig:
    return VlessUserConfig(
        user_uuid=f"uuid-{tag}",
        subscription_url="data:text/plain;base64,QQ==",
        servers=(VlessServerConfig("S1", "NL", "\U0001f1f3\U0001f1f1", "vless://x@nl:443#t"),),
    )


def _result(tag: str) -> VlessProviderResult:
    return VlessProviderResult(outcome=_SUCCESS, config=_cfg(tag))


def _not_found() -> VlessProviderResult:
    return VlessProviderResult(outcome=_NOT_FOUND)


def _make_provider(
    *,
    probe_outcome=None,
    delay: float = 0.0,
) -> tuple[XuiVlessProvider, dict]:
    """Provider whose panel probe is a counting fake. ``probe_outcome`` is a callable
    ``uid -> VlessProviderResult | None`` (None means "no active servers")."""
    p = XuiVlessProvider(pool=object())
    counter = {"n": 0}

    async def fake_probe(uid: str):
        counter["n"] += 1
        if delay:
            await asyncio.sleep(delay)
        if probe_outcome is None:
            return _result(f"v{counter['n']}")
        return probe_outcome(uid)

    p._probe_user_config = fake_probe  # type: ignore[method-assign]
    return p, counter


def test_cold_miss_probes_once_then_served_from_fresh_cache():
    p, counter = _make_provider()
    r1 = _run(p.get_user_config(internal_user_id="u1"))
    r2 = _run(p.get_user_config(internal_user_id="u1"))
    assert r1.outcome == _SUCCESS
    assert r2.outcome == _SUCCESS
    # Second call served from the fresh cache — no second panel probe.
    assert counter["n"] == 1


def test_concurrent_cold_reads_probe_only_once():
    """Two simultaneous cold reads for the same user must not each fan out the probe —
    the per-user lock + double-check collapses them to a single probe."""

    async def scenario():
        return await asyncio.gather(
            p.get_user_config(internal_user_id="u1"),
            p.get_user_config(internal_user_id="u1"),
        )

    p, counter = _make_provider(delay=0.03)
    r1, r2 = _run(scenario())
    assert r1.outcome == _SUCCESS and r2.outcome == _SUCCESS
    assert counter["n"] == 1


def test_stale_entry_is_served_instantly_then_refreshed_in_background():
    p, counter = _make_provider()
    # Seed a stale-but-within-window entry (age ~10 min: past fresh, under stale limit).
    p._cache_epoch["u1"] = 0
    p._config_cache["u1"] = (time.monotonic() - 600, _result("stale"), 0)

    async def scenario():
        served = await p.get_user_config(internal_user_id="u1")  # serves stale, schedules refresh
        await asyncio.sleep(0.02)  # let the background refresh task run
        for task in list(p._refresh_tasks):
            await task
        return served

    served = _run(scenario())
    # Caller got the stale entry instantly (no blocking on the probe) ...
    assert served.config.user_uuid == "uuid-stale"
    # ... while the background refresh probed once and refreshed the cache.
    assert counter["n"] == 1
    assert p._config_cache["u1"][1].config.user_uuid == "uuid-v1"


def test_refresh_is_deduped_while_in_flight():
    p, counter = _make_provider(delay=0.05)
    p._cache_epoch["u1"] = 0
    p._config_cache["u1"] = (time.monotonic() - 600, _result("stale"), 0)

    async def scenario():
        await p.get_user_config(internal_user_id="u1")  # schedules refresh #1
        await p.get_user_config(internal_user_id="u1")  # refresh still in flight -> deduped
        await asyncio.sleep(0.02)
        for task in list(p._refresh_tasks):
            await task

    _run(scenario())
    assert counter["n"] == 1  # only one background probe despite two stale reads


def test_store_config_cache_respects_epoch_guard():
    """An in-flight refresh whose epoch predates an invalidation must not clobber the
    fresher state written by the mutation."""
    p, _counter = _make_provider()
    p._cache_epoch["u1"] = 5
    p._store_config_cache("u1", 5, _result("a"))
    assert p._config_cache["u1"][1].config.user_uuid == "uuid-a"

    # A mutation invalidates (bumping epoch to 6) while a refresh captured epoch 5...
    p._cache_epoch["u1"] = 6
    p._store_config_cache("u1", 5, _result("b"))  # ...so this stale result is dropped.
    assert p._config_cache["u1"][1].config.user_uuid == "uuid-a"


def test_invalidation_clears_entry_and_bumps_epoch():
    p, _counter = _make_provider()
    _run(p.get_user_config(internal_user_id="u1"))
    assert "u1" in p._config_cache
    epoch_before = p._cache_epoch.get("u1", 0)

    p._invalidate_config_cache("u1")

    assert "u1" not in p._config_cache
    assert p._cache_epoch["u1"] == epoch_before + 1


def test_no_active_servers_returns_unavailable_and_not_cached_as_found():
    p, counter = _make_provider(probe_outcome=lambda uid: None)  # no active servers
    r = _run(p.get_user_config(internal_user_id="u1"))
    assert r.outcome == _UNAVAILABLE
    assert counter["n"] == 1


def test_not_found_is_cached_so_repeat_reads_dont_reprobe():
    p, counter = _make_provider(probe_outcome=lambda uid: _not_found())
    r1 = _run(p.get_user_config(internal_user_id="u1"))
    r2 = _run(p.get_user_config(internal_user_id="u1"))
    assert r1.outcome == _NOT_FOUND and r2.outcome == _NOT_FOUND
    assert counter["n"] == 1


def test_invalidation_forces_next_read_to_reprobe():
    p, counter = _make_provider()
    _run(p.get_user_config(internal_user_id="u1"))
    assert counter["n"] == 1

    p._invalidate_config_cache("u1")  # e.g. a renewal / reissue cleared it

    r = _run(p.get_user_config(internal_user_id="u1"))
    assert r.outcome == _SUCCESS
    assert counter["n"] == 2  # cache was cleared -> recomputed
