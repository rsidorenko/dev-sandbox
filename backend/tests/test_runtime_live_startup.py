"""Tests for :mod:`app.runtime.live_startup` (live raw bundle wiring)."""

from __future__ import annotations

import inspect

import app.runtime as rt
import app.runtime.live_startup as live_startup_mod
from app.runtime import accept_mapping_runtime_update
from app.runtime.live_startup import (
    Slice1InMemoryLiveRawRuntimeBundle,
    build_slice1_in_memory_live_raw_runtime_bundle,
    build_slice1_in_memory_live_raw_runtime_bundle_with_default_bridge,
)
from app.runtime.polling import PollingRuntimeConfig
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
        disable_web_page_preview: bool = False,
    ) -> int:
        self.send_calls.append((chat_id, text, correlation_id))
        return 1

    async def send_video(
        self,
        chat_id: int,
        video_path: str,
        *,
        correlation_id: str,
        caption: str | None = None,
        reply_markup=None,
        parse_mode: str | None = None,
    ) -> int:
        return 1

    async def answer_callback_query(self, callback_query_id: str) -> None:
        pass

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        pass

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup=None,
        parse_mode: str | None = None,
    ) -> int:
        return message_id


def test_bundle_builds_with_fake_client_and_bridge() -> None:
    client = FakeRawClient()
    b = build_slice1_in_memory_live_raw_runtime_bundle(client, accept_mapping_runtime_update)
    assert isinstance(b, Slice1InMemoryLiveRawRuntimeBundle)


def test_default_config_when_omitted() -> None:
    client = FakeRawClient()

    async def main() -> None:
        b = build_slice1_in_memory_live_raw_runtime_bundle(client, accept_mapping_runtime_update)
        assert b.config == PollingRuntimeConfig()
        await b.live_loop.run_until_stopped(b.control, max_iterations=1)
        assert client.last_fetch_limit == PollingRuntimeConfig().max_updates_per_batch

    _run(main())


def test_custom_config_preserved_and_used_on_live_iteration() -> None:
    u1 = _update(update_id=1, message=_base_message(text="/start"))
    u2 = _update(update_id=2, message=_base_message(text="/start"))
    u3 = _update(update_id=3, message=_base_message(text="/start"))
    client = FakeRawClient(rounds=[[u1, u2, u3]])
    cfg = PollingRuntimeConfig(max_updates_per_batch=2)
    b = build_slice1_in_memory_live_raw_runtime_bundle(client, accept_mapping_runtime_update, config=cfg)
    assert b.config == cfg

    async def main() -> None:
        summary = await b.live_loop.run_until_stopped(
            b.control,
            correlation_id=new_correlation_id(),
            max_iterations=1,
        )
        assert summary.received_count == 2

    _run(main())


def test_live_loop_uses_same_runtime_instance() -> None:
    client = FakeRawClient()
    b = build_slice1_in_memory_live_raw_runtime_bundle(client, accept_mapping_runtime_update)
    assert b.live_loop._runtime is b.runtime


def test_runner_uses_same_runtime_instance() -> None:
    client = FakeRawClient()
    b = build_slice1_in_memory_live_raw_runtime_bundle(client, accept_mapping_runtime_update)
    assert b.runner._runtime is b.runtime


def test_bridge_in_bundle_is_same_object_passed_in() -> None:
    client = FakeRawClient()

    def br(raw: object):
        return accept_mapping_runtime_update(raw)

    b = build_slice1_in_memory_live_raw_runtime_bundle(client, br)
    assert b.bridge is br


def test_e2e_live_one_iteration_one_start_one_send() -> None:
    raw = _update(message=_base_message(text="/start"))
    client = FakeRawClient(rounds=[[raw]])

    async def main() -> None:
        b = build_slice1_in_memory_live_raw_runtime_bundle(client, accept_mapping_runtime_update)
        summary = await b.live_loop.run_until_stopped(
            b.control,
            correlation_id=new_correlation_id(),
            max_iterations=1,
        )
        assert summary.send_count == 1
        assert len(client.send_calls) == 1

    _run(main())


def test_e2e_two_live_iterations_start_then_status_rejected() -> None:
    uid = 99
    start_u = _update(update_id=1, message=_base_message(user_id=uid, text="/start"))
    status_u = _update(update_id=2, message=_base_message(user_id=uid, text="/status"))
    client = FakeRawClient(rounds=[[start_u], [status_u]])

    async def main() -> None:
        b = build_slice1_in_memory_live_raw_runtime_bundle(client, accept_mapping_runtime_update)
        s1 = await b.live_loop.run_until_stopped(
            b.control,
            correlation_id=new_correlation_id(),
            max_iterations=1,
        )
        s2 = await b.live_loop.run_until_stopped(
            b.control,
            correlation_id=new_correlation_id(),
            max_iterations=1,
        )
        assert s1.send_count == 1 and s2.send_count == 1
        assert len(client.send_calls) == 2
        # /status is now rejected as unknown command
        assert "некорректен" in client.send_calls[1][1].lower()

    _run(main())


def test_convenience_live_bundle_with_default_bridge() -> None:
    client = FakeRawClient()
    b = build_slice1_in_memory_live_raw_runtime_bundle_with_default_bridge(client)
    assert isinstance(b, Slice1InMemoryLiveRawRuntimeBundle)
    assert b.bridge is accept_mapping_runtime_update


def test_convenience_live_one_iteration() -> None:
    raw = _update(message=_base_message(text="/start"))
    client = FakeRawClient(rounds=[[raw]])

    async def main() -> None:
        b = build_slice1_in_memory_live_raw_runtime_bundle_with_default_bridge(client)
        summary = await b.live_loop.run_until_stopped(
            b.control,
            correlation_id=new_correlation_id(),
            max_iterations=1,
        )
        assert summary.send_count == 1
        assert len(client.send_calls) == 1

    _run(main())


def test_app_runtime_exports_live_startup_api() -> None:
    assert hasattr(rt, "Slice1InMemoryLiveRawRuntimeBundle")
    assert hasattr(rt, "build_slice1_in_memory_live_raw_runtime_bundle")
    assert hasattr(rt, "build_slice1_in_memory_live_raw_runtime_bundle_with_default_bridge")
    assert "Slice1InMemoryLiveRawRuntimeBundle" in rt.__all__
    assert "build_slice1_in_memory_live_raw_runtime_bundle" in rt.__all__
    assert "build_slice1_in_memory_live_raw_runtime_bundle_with_default_bridge" in rt.__all__


def test_live_startup_module_excludes_forbidden_tokens() -> None:
    src = inspect.getsource(live_startup_mod)
    lower = src.lower()
    for token in ("billing", "issuance", "admin", "webhook", "signal", "sleep", "sdk"):
        assert token not in lower
