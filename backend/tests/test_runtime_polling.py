"""Tests for slice-1 polling runtime shell (in-memory doubles, no SDK)."""

from __future__ import annotations

import inspect

from app.application.bootstrap import build_slice1_composition
from app.bot_transport import handle_slice1_telegram_update_to_runtime_action as public_handle_slice1
from app.runtime.polling import (
    PollingBatchResult,
    PollingRuntimeConfig,
    Slice1PollingRuntime,
    TelegramPollingClient,
)
from app.security.idempotency import build_bootstrap_idempotency_key
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


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
    """In-memory double: records sends; optional fetch queue."""

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
        reply_markup=None,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> int:
        if self.send_fail:
            raise RuntimeError("send failed")
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


def test_polling_batch_one_start_one_send() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        cid = new_correlation_id()
        rt = Slice1PollingRuntime(c, client)
        raw = _update(message=_base_message(text="/start"))
        r = await rt.process_batch([raw], correlation_id=cid)
        assert r == PollingBatchResult(
            received_count=1,
            send_count=1,
            noop_count=0,
            send_failure_count=0,
            processing_failure_count=0,
        )
        assert len(client.send_calls) == 1
        assert client.send_calls[0][0] == 42
        assert "Добро пожаловать" in client.send_calls[0][1]

    _run(main())


def test_duplicate_start_same_update_id_one_send_one_audit() -> None:
    """Duplicate identical update in one batch: one user-visible send, one UC-01 audit.

    Outbound suppress-send on idempotent replay does **not** fix the case where the first
    Telegram send failed after persistence commit but before delivery; that needs a
    send-ledger / delivery-ledger (non-goal for this slice).
    """

    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        cid = new_correlation_id()
        rt = Slice1PollingRuntime(c, client)
        raw = _update(update_id=5, message=_base_message(user_id=42, text="/start"))
        r = await rt.process_batch([raw, raw], correlation_id=cid)
        assert r.send_count == 1
        assert r.noop_count == 1
        assert len(await c.audit.recorded_events()) == 1

    _run(main())


def test_status_unknown_user_rejected_as_unknown() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        rt = Slice1PollingRuntime(c, client)
        raw = _update(update_id=99, message=_base_message(user_id=999, text="/status"))
        r = await rt.process_batch([raw], correlation_id=new_correlation_id())
        assert r.send_count == 1
        assert client.send_calls[0][0] == 999
        assert "некорректен" in client.send_calls[0][1].lower()

    _run(main())


def test_non_private_and_malformed_noop_no_send() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        rt = Slice1PollingRuntime(c, client)
        group = _update(message=_base_message(text="/start", chat_type="group"))
        bad = {"update_id": 1}
        r = await rt.process_batch([group, bad], correlation_id=new_correlation_id())
        assert r.received_count == 2
        assert r.noop_count == 2
        assert r.send_count == 0
        assert client.send_calls == []

    _run(main())


def test_send_failure_does_not_abort_batch() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        client.send_fail = True
        rt = Slice1PollingRuntime(c, client)
        raw = _update(message=_base_message(text="/start"))
        r = await rt.process_batch([raw], correlation_id=new_correlation_id())
        assert r.send_count == 0
        assert r.send_failure_count == 1
        assert r.processing_failure_count == 0

    _run(main())


def test_processing_failure_continues_batch(monkeypatch) -> None:
    import app.runtime.polling as pr

    orig = pr.handle_slice1_telegram_update_to_runtime_action

    async def flaky(u, composition, *, correlation_id=None):
        if u.get("update_id") == 1:
            raise ValueError("boom")
        return await orig(u, composition, correlation_id=correlation_id)

    async def main() -> None:
        monkeypatch.setattr(pr, "handle_slice1_telegram_update_to_runtime_action", flaky)
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        rt = Slice1PollingRuntime(c, client)
        bad = _update(update_id=1, message=_base_message(text="/start"))
        good = _update(update_id=2, message=_base_message(user_id=7, text="/start"))
        r = await rt.process_batch([bad, good], correlation_id=new_correlation_id())
        assert r.processing_failure_count == 1
        assert r.send_count == 1
        assert client.send_calls[0][0] == 7

    _run(main())


def test_runtime_shell_uses_public_runtime_wrapper() -> None:
    import app.runtime.polling as pr

    assert pr.handle_slice1_telegram_update_to_runtime_action is public_handle_slice1


def test_polling_module_excludes_billing_issuance_admin_webhook() -> None:
    import app.runtime.polling as pol

    src = inspect.getsource(pol)
    lower = src.lower()
    assert "billing" not in lower
    assert "issuance" not in lower
    assert "admin" not in lower
    assert "webhook" not in lower


def test_fake_client_satisfies_protocol() -> None:
    c = FakeTelegramPollingClient()
    assert isinstance(c, TelegramPollingClient)


def test_process_single_update_delegates_to_batch() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        rt = Slice1PollingRuntime(c, client)
        raw = _update(message=_base_message(text="/start"))
        cid = new_correlation_id()
        r1 = await rt.process_single_update(raw, correlation_id=cid)
        r2 = await rt.process_batch([raw], correlation_id=cid)
        assert r1.received_count == 1 and r2.received_count == 1
        assert r1.send_count == 1 and r1.noop_count == 0
        assert r2.send_count == 0 and r2.noop_count == 1

    _run(main())


def test_poll_once_passes_bound_limit_to_fetch() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        cfg = PollingRuntimeConfig(max_updates_per_batch=37)
        rt = Slice1PollingRuntime(c, client, config=cfg)
        await rt.poll_once()
        assert client.fetch_calls == 1
        assert client.last_fetch_limit == 37

    _run(main())


def test_poll_once_empty_fetch_zero_result_no_send() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient(fetch_queue=[])
        rt = Slice1PollingRuntime(c, client)
        r = await rt.poll_once(correlation_id=new_correlation_id())
        assert r == PollingBatchResult(
            received_count=0,
            send_count=0,
            noop_count=0,
            send_failure_count=0,
            processing_failure_count=0,
        )
        assert client.send_calls == []

    _run(main())


def test_poll_once_start_one_send() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        raw = _update(message=_base_message(text="/start"))
        client = FakeTelegramPollingClient(fetch_queue=[raw])
        rt = Slice1PollingRuntime(c, client)
        cid = new_correlation_id()
        r = await rt.poll_once(correlation_id=cid)
        assert r.send_count == 1
        assert len(client.send_calls) == 1
        assert client.send_calls[0][0] == 42
        assert "Добро пожаловать" in client.send_calls[0][1]

    _run(main())


def test_poll_once_duplicate_start_replay_second_poll_noop_one_audit() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        raw = _update(update_id=5, message=_base_message(user_id=42, text="/start"))
        client = FakeTelegramPollingClient(fetch_queue=[raw])
        rt = Slice1PollingRuntime(c, client)
        cid = new_correlation_id()
        r1 = await rt.poll_once(correlation_id=cid)
        r2 = await rt.poll_once(correlation_id=cid)
        assert r1.send_count == 1 and r1.noop_count == 0
        assert r2.send_count == 0 and r2.noop_count == 1
        assert len(await c.audit.recorded_events()) == 1

    _run(main())


def test_poll_once_fetch_failure_returns_safe_result() -> None:
    class FetchExplodingClient(FakeTelegramPollingClient):
        async def fetch_updates(self, *, limit: int):
            raise RuntimeError("fetch failed")

    async def main() -> None:
        c = build_slice1_composition()
        client = FetchExplodingClient()
        rt = Slice1PollingRuntime(c, client)
        r = await rt.poll_once(correlation_id=new_correlation_id())
        assert r == PollingBatchResult(
            received_count=0,
            send_count=0,
            noop_count=0,
            send_failure_count=0,
            processing_failure_count=0,
            fetch_failure_count=1,
        )
        assert client.send_calls == []

    _run(main())


def test_poll_once_delegates_to_process_batch(monkeypatch) -> None:
    async def main() -> None:
        c = build_slice1_composition()
        raw = _update(message=_base_message(text="/start"))
        client = FakeTelegramPollingClient(fetch_queue=[raw])
        rt = Slice1PollingRuntime(c, client)
        delegated: list[tuple[int, object]] = []
        orig = Slice1PollingRuntime.process_batch

        async def spy(self, updates, *, correlation_id=None):
            delegated.append((len(updates), correlation_id))
            return await orig(self, updates, correlation_id=correlation_id)

        monkeypatch.setattr(Slice1PollingRuntime, "process_batch", spy)
        cid = new_correlation_id()
        await rt.poll_once(correlation_id=cid)
        assert delegated == [(1, cid)]

    _run(main())


def test_start_send_failure_then_replay_sends_once_and_marks_sent() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        cid = new_correlation_id()
        rt = Slice1PollingRuntime(c, client)
        raw = _update(update_id=9, message=_base_message(user_id=42, text="/start"))
        client.send_fail = True
        r1 = await rt.process_batch([raw], correlation_id=cid)
        assert r1.send_count == 0 and r1.send_failure_count == 1
        key = build_bootstrap_idempotency_key(42, 9)
        rec1 = await c.outbound_delivery.get_status(key)
        assert rec1 is not None and rec1.status == "pending"
        client.send_fail = False
        r2 = await rt.process_batch([raw], correlation_id=cid)
        assert r2.send_count == 1 and r2.send_failure_count == 0
        rec2 = await c.outbound_delivery.get_status(key)
        assert rec2 is not None and rec2.status == "sent" and rec2.telegram_message_id == 1

    _run(main())


def test_status_path_rejected_as_unknown() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        client = FakeTelegramPollingClient()
        rt = Slice1PollingRuntime(c, client)
        raw = _update(update_id=20, message=_base_message(user_id=500, text="/status"))
        r = await rt.process_batch([raw], correlation_id=new_correlation_id())
        assert r.send_count == 1
        assert "некорректен" in client.send_calls[0][1].lower()

    _run(main())
