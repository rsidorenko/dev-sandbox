"""Tests for :mod:`app.runtime.startup` (in-memory slice-1 bundle wiring)."""

from __future__ import annotations

import asyncio
import inspect

import app.runtime.startup as startup_mod

from app.bot_transport.storefront_ui import text_welcome
from tests.slice1_expected_user_copy import INACTIVE_OR_NOT_ELIGIBLE_TEXT

from app.runtime import (
    PollingRuntimeConfig,
    Slice1InMemoryRuntimeBundle,
    build_slice1_in_memory_runtime_bundle,
)
from app.shared.correlation import new_correlation_id


def _run(coro):
    return asyncio.run(coro)


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


class FakeTelegramPollingClient:
    """In-memory double (aligned with other runtime tests)."""

    __slots__ = ("_fetch_queue", "fetch_calls", "last_fetch_limit", "send_calls", "send_fail")

    def __init__(self, fetch_queue: list[dict[str, object]] | None = None) -> None:
        self._fetch_queue = list(fetch_queue or ())
        self.fetch_calls = 0
        self.last_fetch_limit: int | None = None
        self.send_calls: list[tuple[int, str, str]] = []
        self.send_fail = False

    async def fetch_updates(self, *, limit: int):
        self.fetch_calls += 1
        self.last_fetch_limit = limit
        return list(self._fetch_queue)

    async def send_text_message(
        self,
        chat_id: int,
        text: str,
        *,
        correlation_id: str,
        reply_markup: object | None = None,
    ) -> int:
        if self.send_fail:
            raise RuntimeError("send failed")
        self.send_calls.append((chat_id, text, correlation_id))
        return 1


def test_bundle_builds_with_fake_client() -> None:
    client = FakeTelegramPollingClient()
    b = build_slice1_in_memory_runtime_bundle(client)
    assert isinstance(b, Slice1InMemoryRuntimeBundle)
    assert b.runtime is not None
    assert b.runner is not None


def test_bundle_default_config() -> None:
    client = FakeTelegramPollingClient()
    b = build_slice1_in_memory_runtime_bundle(client)
    assert b.config == PollingRuntimeConfig()
    assert b.config.max_updates_per_batch == 100


def test_bundle_custom_config_used_by_runtime() -> None:
    client = FakeTelegramPollingClient()
    cfg = PollingRuntimeConfig(max_updates_per_batch=37)
    b = build_slice1_in_memory_runtime_bundle(client, config=cfg)
    assert b.config is cfg

    async def main() -> None:
        await b.runtime.poll_once()
        assert client.last_fetch_limit == 37

    _run(main())


def test_runner_uses_same_runtime_instance() -> None:
    client = FakeTelegramPollingClient()
    b = build_slice1_in_memory_runtime_bundle(client)
    assert b.runner._runtime is b.runtime  # noqa: SLF001


def test_bundle_e2e_start_send_then_status_fail_closed() -> None:
    async def main() -> None:
        client = FakeTelegramPollingClient()
        bundle = build_slice1_in_memory_runtime_bundle(client)
        cid = new_correlation_id()
        uid = 77
        await bundle.runtime.process_batch(
            [_update(update_id=2, message=_base_message(user_id=uid, text="/start"))],
            correlation_id=cid,
        )
        assert len(client.send_calls) == 1
        assert client.send_calls[0][1] == text_welcome()
        await bundle.runtime.process_batch(
            [_update(message=_base_message(user_id=uid, text="/status"))],
            correlation_id=cid,
        )
        assert len(client.send_calls) == 2
        assert client.send_calls[1][0] == uid
        assert client.send_calls[1][1] == INACTIVE_OR_NOT_ELIGIBLE_TEXT

    _run(main())


def test_startup_module_source_excludes_forbidden_terms() -> None:
    src = inspect.getsource(startup_mod)
    lower = src.lower()
    assert "billing" not in lower
    assert "issuance" not in lower
    assert "admin" not in lower
    assert "webhook" not in lower


def test_runtime_package_exports_bundle_api() -> None:
    import app.runtime as rt

    assert rt.Slice1InMemoryRuntimeBundle is Slice1InMemoryRuntimeBundle
    assert rt.build_slice1_in_memory_runtime_bundle is build_slice1_in_memory_runtime_bundle
    assert "Slice1InMemoryRuntimeBundle" in rt.__all__
    assert "build_slice1_in_memory_runtime_bundle" in rt.__all__


def test_runner_run_iterations_invokes_bundled_runtime_poll_once(monkeypatch) -> None:
    from app.runtime.polling import PollingBatchResult, Slice1PollingRuntime

    async def main() -> None:
        client = FakeTelegramPollingClient()
        bundle = build_slice1_in_memory_runtime_bundle(client)
        called: list[object] = []

        async def spy(*args, **kwargs):
            called.append(True)
            return PollingBatchResult(
                received_count=0,
                send_count=0,
                noop_count=0,
                send_failure_count=0,
                processing_failure_count=0,
            )

        monkeypatch.setattr(Slice1PollingRuntime, "poll_once", spy)
        await bundle.runner.run_iterations(1)
        assert called == [True]

    _run(main())
