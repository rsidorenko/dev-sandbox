"""Tests for :mod:`app.runtime.raw_startup` (in-memory raw bundle wiring)."""

from __future__ import annotations

import inspect

import app.runtime as rt
import app.runtime.raw_startup as raw_startup_mod
from app.runtime import accept_mapping_runtime_update
from app.runtime.polling import PollingRuntimeConfig
from app.runtime.raw_startup import (
    Slice1InMemoryRawRuntimeBundle,
    build_slice1_in_memory_raw_runtime_bundle,
    build_slice1_in_memory_raw_runtime_bundle_with_default_bridge,
)
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run
from tests.slice1_expected_user_copy import INACTIVE_OR_NOT_ELIGIBLE_TEXT


def _base_message(*, text: str, user_id: int = 42, chat_type: str = "private") -> dict[str, object]:
    return {
        "message_id": 1,
        "from": {"id": user_id, "is_bot": False, "first_name": "U"},
        "chat": {"id": user_id, "type": chat_type},
        "text": text,
    }


def _update(
    *,
    update_id: int = 1,
    message: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    u: dict[str, object] = {"update_id": update_id, "message": message}
    u.update(extra)
    return u


class FakeRawClient:
    """In-memory raw client; optional per-fetch queues; respects ``limit`` on returned slice."""

    __slots__ = ("_ri", "_rounds", "fetch_calls", "last_fetch_limit", "send_calls")

    def __init__(self, rounds: list[list[dict[str, object]]] | None = None) -> None:
        self._rounds = [list(x) for x in (rounds or [])]
        self._ri = 0
        self.fetch_calls = 0
        self.last_fetch_limit: int | None = None
        self.send_calls: list[tuple[int, str, str]] = []

    async def fetch_raw_updates(self, *, limit: int, offset: int | None = None) -> list[dict[str, object]]:
        self.fetch_calls += 1
        self.last_fetch_limit = limit
        if self._ri >= len(self._rounds):
            return []
        batch = self._rounds[self._ri]
        self._ri += 1
        return list(batch[:limit])

    async def send_text_message(
        self,
        chat_id: int,
        text: str,
        *,
        correlation_id: str,
        reply_markup: object | None = None,
        parse_mode: str | None = None,
    ) -> int:
        self.send_calls.append((chat_id, text, correlation_id))
        return 1


def test_bundle_builds_with_fake_client_and_bridge() -> None:
    client = FakeRawClient()
    b = build_slice1_in_memory_raw_runtime_bundle(client, accept_mapping_runtime_update)
    assert isinstance(b, Slice1InMemoryRawRuntimeBundle)


def test_default_config_when_omitted() -> None:
    client = FakeRawClient()
    b = build_slice1_in_memory_raw_runtime_bundle(client, accept_mapping_runtime_update)
    assert b.config == PollingRuntimeConfig()

    async def main() -> None:
        await b.runtime.poll_once()

    _run(main())
    assert client.last_fetch_limit == PollingRuntimeConfig().max_updates_per_batch


def test_custom_config_preserved_and_used_on_poll_once() -> None:
    u1 = _update(update_id=1, message=_base_message(text="/start"))
    u2 = _update(update_id=2, message=_base_message(text="/start"))
    u3 = _update(update_id=3, message=_base_message(text="/start"))
    client = FakeRawClient(rounds=[[u1, u2, u3]])
    cfg = PollingRuntimeConfig(max_updates_per_batch=2)
    b = build_slice1_in_memory_raw_runtime_bundle(client, accept_mapping_runtime_update, config=cfg)
    assert b.config == cfg

    async def main() -> None:
        r = await b.runtime.poll_once(correlation_id=new_correlation_id())
        assert r.raw_received_count == 2

    _run(main())


def test_runner_uses_same_runtime_instance() -> None:
    client = FakeRawClient()
    b = build_slice1_in_memory_raw_runtime_bundle(client, accept_mapping_runtime_update)
    assert b.runner._runtime is b.runtime


def test_bridge_in_bundle_is_same_object_passed_in() -> None:
    client = FakeRawClient()

    def br(raw: object):
        return accept_mapping_runtime_update(raw)

    b = build_slice1_in_memory_raw_runtime_bundle(client, br)
    assert b.bridge is br


def test_one_raw_start_poll_once_one_send() -> None:
    raw = _update(message=_base_message(text="/start"))
    client = FakeRawClient(rounds=[[raw]])

    async def main() -> None:
        b = build_slice1_in_memory_raw_runtime_bundle(client, accept_mapping_runtime_update)
        r = await b.runtime.poll_once(correlation_id=new_correlation_id())
        assert r.send_count == 1
        assert len(client.send_calls) == 1

    _run(main())


def test_duplicate_raw_start_two_poll_once_replay_second_noop_one_audit() -> None:
    raw = _update(update_id=5, message=_base_message(user_id=42, text="/start"))
    client = FakeRawClient(rounds=[[raw], [raw]])

    async def main() -> None:
        b = build_slice1_in_memory_raw_runtime_bundle(client, accept_mapping_runtime_update)
        cid = new_correlation_id()
        r1 = await b.runtime.poll_once(correlation_id=cid)
        r2 = await b.runtime.poll_once(correlation_id=cid)
        assert r1.send_count == 1 and r1.noop_count == 0
        assert r2.send_count == 0 and r2.noop_count == 1
        assert len(await b.composition.audit.recorded_events()) == 1

    _run(main())


def test_raw_status_after_bootstrap_no_snapshot_fail_closed() -> None:
    uid = 99
    start_u = _update(update_id=1, message=_base_message(user_id=uid, text="/start"))
    status_u = _update(update_id=2, message=_base_message(user_id=uid, text="/status"))
    client = FakeRawClient(rounds=[[start_u], [status_u]])
    # Same copy as ``OutboundMessageKey.INACTIVE_OR_NOT_ELIGIBLE`` in message catalog (fail-closed UC-02).
    inactive = INACTIVE_OR_NOT_ELIGIBLE_TEXT

    async def main() -> None:
        b = build_slice1_in_memory_raw_runtime_bundle(client, accept_mapping_runtime_update)
        await b.runtime.poll_once(correlation_id=new_correlation_id())
        await b.runtime.poll_once(correlation_id=new_correlation_id())
        assert len(client.send_calls) == 2
        assert client.send_calls[1][1] == inactive

    _run(main())


def test_convenience_bundle_with_default_bridge() -> None:
    client = FakeRawClient()
    b = build_slice1_in_memory_raw_runtime_bundle_with_default_bridge(client)
    assert isinstance(b, Slice1InMemoryRawRuntimeBundle)
    assert b.bridge is accept_mapping_runtime_update


def test_convenience_bundle_e2e_start_one_send() -> None:
    raw = _update(message=_base_message(text="/start"))
    client = FakeRawClient(rounds=[[raw]])

    async def main() -> None:
        b = build_slice1_in_memory_raw_runtime_bundle_with_default_bridge(client)
        r = await b.runtime.poll_once(correlation_id=new_correlation_id())
        assert r.send_count == 1
        assert len(client.send_calls) == 1

    _run(main())


def test_app_runtime_exports_raw_startup_api() -> None:
    assert hasattr(rt, "Slice1InMemoryRawRuntimeBundle")
    assert hasattr(rt, "build_slice1_in_memory_raw_runtime_bundle")
    assert hasattr(rt, "build_slice1_in_memory_raw_runtime_bundle_with_default_bridge")
    assert "Slice1InMemoryRawRuntimeBundle" in rt.__all__
    assert "build_slice1_in_memory_raw_runtime_bundle" in rt.__all__
    assert "build_slice1_in_memory_raw_runtime_bundle_with_default_bridge" in rt.__all__


def test_raw_startup_module_excludes_forbidden_tokens() -> None:
    src = inspect.getsource(raw_startup_mod)
    lower = src.lower()
    assert "billing" not in lower
    assert "issuance" not in lower
    assert "admin" not in lower
    assert "webhook" not in lower
