"""Юнит-тесты рассылки: чистая логика (HTML-валидатор, категории, SQL-билдер,
retry_after) + BroadcastRunner на фейковом пуле/клиенте (без БД и сети).

Интеграционные тесты SQL-резолвера против PostgreSQL — см. test_admin_broadcast_integration.py.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.web_api.admin_broadcast import (
    BroadcastRunner,
    _build_inner_union,
    _build_reply_markup,
    _count_sql,
    _extract_retry_after,
    _fetch_sql,
    _validate_buttons,
    _validate_categories,
    _validate_html,
)

# --- _validate_html ---------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("plain text", None),
        ("use <code>token</code> here", None),
        ('click <a href="https://t.me/bravada">here</a>', None),
        ('<a href="mailto:a@b.ru">m</a>', None),
        ("<b>bold <i>both</i></b>", None),
        ("<a>no href</a>", "link_missing_href"),
        ('<a href="javascript:alert(1)">x</a>', "link_bad_scheme:javascript"),
        ("<script>x</script>", "unsupported_tag:script"),
        ("<b>unclosed", "unclosed_tag:b"),
    ],
)
def test_validate_html(text, expected):
    assert _validate_html(text) == expected


# --- _validate_categories ---------------------------------------------------


def test_validate_categories_accepts_known():
    result = _validate_categories(["all", "active", "dormant"])
    assert set(result) == {"all", "active", "dormant"}


def test_validate_categories_rejects_unknown():
    assert _validate_categories(["all", "bogus"]) is None


def test_validate_categories_rejects_non_list():
    assert _validate_categories("all") is None
    assert _validate_categories(None) is None


def test_validate_categories_empty_is_empty_list():
    assert _validate_categories([]) == []


# --- SQL builder ------------------------------------------------------------


def test_build_inner_union_only_one_param_token():
    sql = _build_inner_union(["all", "expired_0_2d", "dormant"])
    # Единственный параметр — $1 (массив явных tg-id); интервалы/now() — литералы.
    assert sql.count("$1") == 1
    assert "user_identities" in sql
    assert "INTERVAL '3 days'" in sql


def test_count_and_fetch_sql_wrap_distinct_and_filter_positive():
    for fn in (_count_sql, _fetch_sql):
        sql = fn(["all"])
        assert "telegram_user_id > 0" in sql
        assert "DISTINCT" in sql


# --- _extract_retry_after ---------------------------------------------------


def _http_response(status: int, body: bytes = b"") -> httpx.Response:
    req = httpx.Request("POST", "https://api.telegram.org/botx/sendMessage")
    return httpx.Response(status, request=req, content=body)


def test_extract_retry_after_present():
    resp = _http_response(429, b'{"ok":false,"parameters":{"retry_after":7}}')
    assert _extract_retry_after(resp) == 7


def test_extract_retry_after_absent_defaults_to_one():
    resp = _http_response(429, b'{"ok":false,"description":"too many requests"}')
    assert _extract_retry_after(resp) == 1


def test_extract_retry_after_non_json_defaults_to_one():
    resp = _http_response(429, b"not json")
    assert _extract_retry_after(resp) == 1


# --- BroadcastRunner on fakes ----------------------------------------------


class _FakePool:
    """Запоминает UPDATE-вызовы и имитирует конфиг задачи + список получателей."""

    def __init__(self, job_row: dict, recipients: list[int]) -> None:
        self._job_row = job_row
        self._recipients = [{"telegram_user_id": r} for r in recipients]
        self.total_recipients_written: int | None = None
        self.finish: tuple | None = None  # (status, sent, blocked, failed, last_error)

    async def execute(self, sql: str, *args):
        s = sql.upper()
        if s.startswith("UPDATE BROADCAST_JOBS SET STATUS='RUNNING'"):
            return "UPDATE 1"
        if "TOTAL_RECIPIENTS=$2" in s:
            self.total_recipients_written = args[1]
            return "UPDATE 1"
        if s.startswith("UPDATE BROADCAST_JOBS SET STATUS=$2"):
            # finish: status=$2 sent=$3 blocked=$4 failed=$5 ... last_error=$6
            self.finish = (args[1], args[2], args[3], args[4], args[5])
            return "UPDATE 1"
        return "UPDATE 1"  # flush и прочее

    async def fetchrow(self, sql: str, *args):
        return self._job_row

    async def fetch(self, sql: str, *args):
        return list(self._recipients)


class _FakeClient:
    """behavior: chat_id -> list of outcomes (popped per attempt); [] => ok.
    Outcome: "ok" | ("http", status, body_bytes) | ("net",).
    """

    def __init__(self, behaviors: dict | None = None) -> None:
        self.behaviors = {k: list(v) for k, v in (behaviors or {}).items()}
        self.calls: list[int] = []
        self.markups: list = []  # reply_markup, переданный в каждый вызов

    async def send_text_message(
        self,
        *,
        chat_id,
        text,
        correlation_id,
        parse_mode=None,
        disable_web_page_preview=False,
        reply_markup=None,
    ):
        self.calls.append(chat_id)
        self.markups.append(reply_markup)
        seq = self.behaviors.get(chat_id)
        outcome = seq.pop(0) if seq else "ok"
        if outcome == "ok":
            return chat_id
        if isinstance(outcome, tuple) and outcome[0] == "http":
            _, status, body = outcome
            req = httpx.Request("POST", "https://api.telegram.org/botx/sendMessage")
            resp = httpx.Response(status, request=req, content=body)
            raise httpx.HTTPStatusError(f"http {status}", request=req, response=resp)
        if isinstance(outcome, tuple) and outcome[0] == "net":
            raise httpx.ConnectError("net down")
        raise AssertionError(f"unknown outcome {outcome!r}")


async def _nosleep(_s: float = 0.0) -> None:
    return None


def _runner(
    client: _FakeClient,
    recipients: list[int],
    *,
    buttons: list | None = None,
) -> tuple[BroadcastRunner, _FakePool]:
    pool = _FakePool(
        job_row={
            "categories": ["all"],
            "explicit_telegram_ids": [],
            "message_text": "<b>hi</b>",
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "buttons": list(buttons or []),
        },
        recipients=recipients,
    )
    runner = BroadcastRunner(pool=pool, client=client, job_id="11111111-2222-3333-4444-555555555555", sleep=_nosleep)
    return runner, pool


def _run(coro):
    return asyncio.run(coro)


def test_runner_all_ok_completes():
    client = _FakeClient()
    runner, pool = _runner(client, [11, 12, 13, 14, 15])
    _run(runner.run())
    assert pool.finish[0] == "completed"
    assert pool.finish[1] == 5  # sent
    assert pool.finish[2] == 0 and pool.finish[3] == 0  # blocked, failed
    assert pool.total_recipients_written == 5
    assert client.calls == [11, 12, 13, 14, 15]


def test_runner_403_counts_as_blocked_not_failed():
    client = _FakeClient({13: [("http", 403, b"")]})
    runner, pool = _runner(client, [11, 12, 13, 14, 15])
    _run(runner.run())
    assert pool.finish[0] == "completed"
    assert pool.finish[1] == 4  # sent
    assert pool.finish[2] == 1  # blocked
    assert pool.finish[3] == 0  # failed


def test_runner_400_content_error_aborts_whole_job():
    # «can't parse entities» — проблема разметки, повторится на всех. Аварийный стоп.
    body = b'{"ok":false,"description":"Bad Request: can\'t parse entities: unsupported tag"}'
    client = _FakeClient({13: [("http", 400, body)]})
    runner, pool = _runner(client, [11, 12, 13, 14, 15])
    _run(runner.run())
    assert pool.finish[0] == "failed"
    assert pool.finish[1] == 2  # sent (11, 12) before hitting 13
    assert "bad_request" in (pool.finish[4] or "")
    # Отправка остановилась на первом content-400 — 14 и 15 не получили сообщение.
    assert client.calls == [11, 12, 13]


def test_runner_400_recipient_error_continues():
    # «chat not found» — проблема одного чата, помечаем failed и идём дальше.
    body = b'{"ok":false,"description":"Bad Request: chat not found"}'
    client = _FakeClient({13: [("http", 400, body)]})
    runner, pool = _runner(client, [11, 12, 13, 14, 15])
    _run(runner.run())
    assert pool.finish[0] == "completed"
    assert pool.finish[1] == 4  # sent (11, 12, 14, 15)
    assert pool.finish[3] == 1  # failed (13)
    # Все получатели опрошены.
    assert client.calls == [11, 12, 13, 14, 15]


def test_runner_429_then_ok_retries_successfully():
    body = b'{"ok":false,"parameters":{"retry_after":1}}'
    client = _FakeClient({12: [("http", 429, body)]})  # один 429, затем ok
    runner, pool = _runner(client, [11, 12, 13])
    _run(runner.run())
    assert pool.finish[0] == "completed"
    assert pool.finish[1] == 3  # все дошли (12 — после ретрая)


def test_runner_persistent_429_counts_as_failed():
    body = b'{"ok":false,"parameters":{"retry_after":1}}'
    client = _FakeClient({12: [("http", 429, body)] * 3})  # 3×429 → rate_limited
    runner, pool = _runner(client, [11, 12, 13])
    _run(runner.run())
    assert pool.finish[0] == "completed"
    assert pool.finish[1] == 2  # sent (11, 13)
    assert pool.finish[3] == 1  # failed (12)


def test_runner_network_transient_retries_then_transient_fail():
    client = _FakeClient({12: [("net",)] * 4})  # >3 сетевых → transient
    runner, pool = _runner(client, [11, 12, 13])
    _run(runner.run())
    assert pool.finish[0] == "completed"
    assert pool.finish[1] == 2  # sent (11, 13)
    assert pool.finish[3] == 1  # failed (12)


def test_runner_zero_recipients_completes_without_sending():
    client = _FakeClient()
    runner, pool = _runner(client, [])
    _run(runner.run())
    assert pool.finish[0] == "completed"
    assert pool.finish[1] == 0
    assert client.calls == []


def test_runner_preempted_queued_cas_exits_silently():
    """Если CAS queued→running не сработал (UPDATE 0) — задача не стартует."""
    pool = _FakePool(
        job_row={
            "categories": [],
            "explicit_telegram_ids": [],
            "message_text": "x",
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        recipients=[1],
    )

    async def execute(sql, *args):
        if sql.upper().startswith("UPDATE BROADCAST_JOBS SET STATUS='RUNNING'"):
            return "UPDATE 0"  # уже не queued
        return "UPDATE 1"

    pool.execute = execute  # type: ignore[method-assign]
    runner = BroadcastRunner(
        pool=pool, client=_FakeClient(), job_id="00000000-0000-0000-0000-000000000000", sleep=_nosleep
    )
    _run(runner.run())
    assert pool.finish is None  # ничего не финишировал


# --- Кнопки под сообщением ---------------------------------------------------


def test_build_reply_markup_empty_is_none():
    assert _build_reply_markup([]) is None
    assert _build_reply_markup(None) is None


def test_build_reply_markup_action_and_url_rows():
    buttons = [
        {"text": "🔑 Купить VPN", "callback_data": "buy_vpn"},
        {"text": "Сайт", "url": "https://example.com"},
    ]
    markup = _build_reply_markup(buttons)
    assert markup == {
        "inline_keyboard": [
            [{"text": "🔑 Купить VPN", "callback_data": "buy_vpn"}],
            [{"text": "Сайт", "url": "https://example.com"}],
        ]
    }


def test_validate_buttons_actions_known_only_and_dedup():
    buttons, err = _validate_buttons({"action_buttons": ["buy_vpn", "main_menu", "buy_vpn", "bogus"]})
    assert err is None
    # неизвестный отброшен, дубликата нет, порядок сохранён
    assert buttons == [
        {"text": "🔑 Купить VPN", "callback_data": "buy_vpn"},
        {"text": "🏠 Главное меню", "callback_data": "main_menu"},
    ]


def test_validate_buttons_url_requires_http_https():
    _, err = _validate_buttons({"url_buttons": [{"label": "x", "url": "javascript:alert(1)"}]})
    assert err is not None and err.status_code == 400
    buttons, err = _validate_buttons({"url_buttons": [{"label": "Сайт", "url": "https://example.com"}]})
    assert err is None
    assert buttons == [{"text": "Сайт", "url": "https://example.com"}]


def test_validate_buttons_actions_then_urls_order_and_cap():
    # 2 действия + 1 url — суммарно в пределах лимита
    buttons, err = _validate_buttons(
        {"action_buttons": ["main_menu"], "url_buttons": [{"label": "L", "url": "http://x.io"}]}
    )
    assert err is None
    assert buttons[0]["callback_data"] == "main_menu"
    assert buttons[1]["url"] == "http://x.io"
    # превышение лимита (8) → too_many_buttons
    many = {"action_buttons": ["buy_vpn"], "url_buttons": [{"label": "u", "url": "http://x.io"}] * 8}
    _, err = _validate_buttons(many)
    assert err is not None and err.status_code == 400


def test_validate_buttons_empty_payload_is_empty_list():
    buttons, err = _validate_buttons({})
    assert err is None and buttons == []


def test_runner_forwards_reply_markup_when_buttons_present():
    buttons = [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]
    client = _FakeClient()
    runner, _pool = _runner(client, [11, 12], buttons=buttons)
    _run(runner.run())
    # Каждый вызов получил ненулевой markup с нашей кнопкой
    assert len(client.markups) == 2
    for markup in client.markups:
        assert markup == {"inline_keyboard": [[{"text": "🏠 Главное меню", "callback_data": "main_menu"}]]}


def test_runner_no_markup_without_buttons():
    client = _FakeClient()
    runner, _pool = _runner(client, [11])
    _run(runner.run())
    assert client.markups == [None]
