"""Юнит-тесты рассылки: чистая логика (HTML-валидатор, категории, SQL-билдер,
retry_after) + BroadcastRunner на фейковом пуле/клиенте (без БД и сети).

Интеграционные тесты SQL-резолвера против PostgreSQL — см. test_admin_broadcast_integration.py.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.bot_transport.storefront_ui import CB_BROADCAST_DAYS, CB_BROADCAST_PROMO
from app.web_api.admin_broadcast import (
    _CATEGORY_PREDICATES,
    _WEB_CATEGORY_PREDICATES,
    BroadcastRunner,
    _build_inner_union,
    _build_reply_markup,
    _cb,
    _count_sql,
    _decode_bonus,
    _decode_buttons,
    _extract_retry_after,
    _fetch_sql,
    _html_for_telegram,
    _resolve_explicit_targets,
    _validate_bonus_config,
    _validate_bonus_promo_exists,
    _validate_buttons,
    _validate_categories,
    _validate_html,
    _web_count_sql,
    _web_fetch_sql,
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
        ("a<br>b", None),
        ("line1<br>line2", None),
        ("<br/>", None),
        ("<a>no href</a>", "link_missing_href"),
        ('<a href="javascript:alert(1)">x</a>', "link_bad_scheme:javascript"),
        ("<script>x</script>", "unsupported_tag:script"),
        # Незакрытые разрешённые теги НЕ блокируются — автозакроются в _html_for_telegram
        ("<b>unclosed", None),
        ("<i><b>nested no close", None),
    ],
)
def test_validate_html(text, expected):
    assert _validate_html(text) == expected


def test_html_for_telegram_converts_br_to_newline():
    assert _html_for_telegram("a<br>b") == "a\nb"
    assert _html_for_telegram("a<br/>b") == "a\nb"
    assert _html_for_telegram("a<BR>b") == "a\nb"
    assert _html_for_telegram("a<br />b") == "a\nb"
    assert _html_for_telegram('a<br class="x">b') == "a\nb"  # br с атрибутом
    # без <br> и без незакрытых — без изменений
    assert _html_for_telegram("обычный <b>текст</b>") == "обычный <b>текст</b>"
    # несколько <br>
    assert _html_for_telegram("x<br>y<br>z") == "x\ny\nz"


def test_html_for_telegram_autocloses_unclosed_tags():
    # один незакрытый
    assert _html_for_telegram("<b>text") == "<b>text</b>"
    # вложенные незакрытые — закрываются в обратном порядке
    assert _html_for_telegram("<b><i>x") == "<b><i>x</i></b>"
    # br + незакрытый: сначала конвертация, потом автозакрытие
    assert _html_for_telegram("line1<br><b>bold") == "line1\n<b>bold</b>"
    # уже сбалансировано — ничего не добавляется
    assert _html_for_telegram("<b>x</b>") == "<b>x</b>"


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


def test_web_predicates_mirror_tg_without_sign_filter():
    """Web-варианты категорий = TG-варианты без знакового фильтра (тот же core)."""
    assert set(_CATEGORY_PREDICATES) == set(_WEB_CATEGORY_PREDICATES)
    for key, tg_sql in _CATEGORY_PREDICATES.items():
        assert tg_sql.replace("i.telegram_user_id > 0", "TRUE") == _WEB_CATEGORY_PREDICATES[key]


def test_web_count_and_fetch_sql_filter_by_verified_email():
    """Web-аудитория: EXISTS verified email; без `> 0` (web-only с отрицательным tg-id)."""
    for fn in (_web_count_sql, _web_fetch_sql):
        sql = fn(["all"])
        assert "user_emails" in sql
        assert "is_verified = TRUE" in sql
        assert "DISTINCT" in sql
        assert "telegram_user_id > 0" not in sql
        assert sql.count("$1") == 1


# --- _resolve_explicit_targets (функция чистая, пул подменяем) ---------------


class _EmailResolvePool:
    """fetchval(email) → telegram_user_id (или None). Помнит SQL для ассертов."""

    def __init__(self, mapping: dict[str, int | None]) -> None:
        self._mapping = {k.lower(): v for k, v in mapping.items()}
        self.email_sql: str | None = None

    async def fetchval(self, sql: str, *args):
        self.email_sql = sql
        return self._mapping.get(str(args[0]).lower())


def test_resolve_explicit_targets_numbers_and_emails():
    pool = _EmailResolvePool({"web@example.com": -42, "tg@example.com": 777})
    resolved, unresolved = _run(
        _resolve_explicit_targets(pool, ["12345", "-9", "0", "web@example.com", "tg@example.com", "unknown@x.io"])
    )
    assert resolved == [12345, -42, 777]  # web-only (отрицательный) тоже резолвится
    # числа обрабатываются раньше email — потому «0» и «-9» в начале
    assert unresolved == ["0", "-9", "unknown@x.io"]


def test_resolve_explicit_targets_email_sql_has_no_sign_filter():
    """Email-резолв НЕ фильтрует по знаку tg-id (web-only имеют право на рассылку)."""
    pool = _EmailResolvePool({"a@b.io": 1})
    _run(_resolve_explicit_targets(pool, ["a@b.io"]))
    assert pool.email_sql is not None
    assert "telegram_user_id > 0" not in pool.email_sql
    assert "is_verified = TRUE" in pool.email_sql


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


class _FakeConn:
    """Соединение из pool.acquire(): поддерживает transaction() + executemany()
    (нужно BroadcastRunner._seed_bonus_claims) + execute/fetchrow для web-канала
    (PostgresWebNotificationRepository.insert_broadcast_for_tg_ids)."""

    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def transaction(self):
        return self  # сам выступает async-context-менеджером транзакции

    async def executemany(self, sql: str, rows):
        # rows: (job_id, telegram_user_id, days|code, ttl_days) — от TG- и web-посева
        if self._pool.bonus_seed_rows is None:
            self._pool.bonus_seed_rows = []
        self._pool.bonus_seed_rows.extend(rows)

    async def execute(self, sql: str, *args):
        s = sql.upper()
        if s.startswith("INSERT INTO WEB_NOTIFICATIONS"):
            # (telegram_user_ids, title, body, payload_json) — web fan-out
            if self._pool.fail_web_insert:
                raise RuntimeError("web insert failed")
            self._pool.web_notification_inserts.append(
                {"tg_ids": list(args[0]), "title": args[1], "body": args[2], "payload": args[3]}
            )
            return f"INSERT 0 {len(args[0])}"
        return "UPDATE 1"


class _FakePool:
    """Запоминает UPDATE-вызовы и имитирует конфиг задачи + списки получателей
    (TG- и web-аудитория — отдельно: fetch различает по user_emails в SQL)."""

    def __init__(self, job_row: dict, recipients: list[int], web_recipients: list[int] | None = None) -> None:
        self._job_row = job_row
        self._recipients = [{"telegram_user_id": r} for r in recipients]
        self._web_recipients = [{"telegram_user_id": r} for r in (web_recipients if web_recipients is not None else [])]
        self.total_recipients_written: int | None = None
        self.finish: tuple | None = None  # (status, sent, blocked, failed, last_error)
        # Записанные _seed_*_claims строки (job_id, tg, days|code, ttl); None если не вызывалось.
        self.bonus_seed_rows: list | None = None
        # web fan-out: вставки web_notifications — {tg_ids, title, body, payload}
        self.web_notification_inserts: list[dict] = []
        self.web_recipient_count_written: int | None = None
        self.web_notified_count_written: int | None = None
        self.fail_web_insert = False  # симуляция сбоя веб-канала

    def acquire(self):
        return _FakeConn(self)

    async def execute(self, sql: str, *args):
        s = sql.upper()
        if s.startswith("UPDATE BROADCAST_JOBS SET STATUS='RUNNING'"):
            return "UPDATE 1"
        if "TOTAL_RECIPIENTS=$2" in s:
            self.total_recipients_written = args[1]
            return "UPDATE 1"
        if "WEB_RECIPIENT_COUNT=$2" in s:
            self.web_recipient_count_written = args[1]
            return "UPDATE 1"
        if "WEB_NOTIFIED_COUNT=$2" in s:
            self.web_notified_count_written = args[1]
            return "UPDATE 1"
        if s.startswith("UPDATE BROADCAST_JOBS SET STATUS=$2"):
            # finish: status=$2 sent=$3 blocked=$4 failed=$5 ... last_error=$6
            self.finish = (args[1], args[2], args[3], args[4], args[5])
            return "UPDATE 1"
        return "UPDATE 1"  # flush и прочее

    async def fetchrow(self, sql: str, *args):
        return self._job_row

    async def fetch(self, sql: str, *args):
        # web-аудитория отличается по user_emails в SQL; дефолт — пустая (TG-only тесты)
        if "user_emails" in sql.lower():
            return list(self._web_recipients)
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
    bonus: dict | str | None = None,
    web_recipients: list[int] | None = None,
    explicit_ids: list[int] | None = None,
) -> tuple[BroadcastRunner, _FakePool]:
    pool = _FakePool(
        job_row={
            "categories": ["all"],
            "explicit_telegram_ids": list(explicit_ids or []),
            "message_text": "<b>hi</b>",
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "buttons": list(buttons or []),
            "bonus": bonus,
        },
        recipients=recipients,
        web_recipients=web_recipients,
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


def test_validate_buttons_reads_nested_shape_like_frontend():
    # Регресс: фронт шлёт кнопки ВЛОЖЕННО {buttons:{action_buttons,url_buttons}},
    # а не верхним уровнем. Хендлер извлекает data["buttons"] перед вызовом.
    frontend_body = {
        "message_text": "x",
        "buttons": {"action_buttons": ["buy_vpn", "main_menu"], "url_buttons": []},
    }
    buttons, err = _validate_buttons(frontend_body.get("buttons") or {})
    assert err is None
    assert [b["callback_data"] for b in buttons] == ["buy_vpn", "main_menu"]


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


# --- Декодирование JSONB кнопок (asyncpg без кодека отдаёт str) --------------


def test_decode_buttons_from_str_list_none():
    import json

    assert _decode_buttons(None) == []
    assert _decode_buttons("") == []
    assert _decode_buttons("[]") == []
    assert _decode_buttons([{"text": "A", "callback_data": "main_menu"}]) == [
        {"text": "A", "callback_data": "main_menu"}
    ]
    decoded = _decode_buttons(json.dumps([{"text": "A", "callback_data": "main_menu"}]))
    assert decoded == [{"text": "A", "callback_data": "main_menu"}]
    # битой JSON-строке не падает, возвращает []
    assert _decode_buttons("{not json") == []


def test_runner_decodes_buttons_from_json_string_like_asyncpg():
    """Регрессия: asyncpg без jsonb-кодека отдаёт buttons как str. Runner должен
    json.loads-нуть его, а не list(str) → символы."""
    import json

    buttons_json = json.dumps([{"text": "🏠 Главное меню", "callback_data": "main_menu"}])
    pool = _FakePool(
        job_row={
            "categories": ["all"],
            "explicit_telegram_ids": [],
            "message_text": "<b>hi</b>",
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "buttons": buttons_json,  # str, как от реального asyncpg
        },
        recipients=[11],
    )
    client = _FakeClient()
    runner = BroadcastRunner(
        pool=pool,
        client=client,
        job_id="22222222-3333-4444-5555-666666666666",
        sleep=_nosleep,
    )
    _run(runner.run())
    assert len(client.markups) == 1
    assert client.markups[0] == {"inline_keyboard": [[{"text": "🏠 Главное меню", "callback_data": "main_menu"}]]}


# --- Бонус-кнопка рассылки (бесплатные дни) ---------------------------------


def test_validate_bonus_config_none_and_empty_are_valid_none():
    bonus, err = _validate_bonus_config(None)
    assert err is None and bonus is None
    bonus, err = _validate_bonus_config({})
    assert err is None and bonus is None


def test_validate_bonus_config_happy_path_normalizes_defaults():
    bonus, err = _validate_bonus_config({"type": "days", "days": 7, "label": "  Получить 7 дней  "})
    assert err is None
    assert bonus == {"type": "days", "days": 7, "label": "Получить 7 дней", "ttl_days": 30}


def test_validate_bonus_config_respects_explicit_ttl():
    bonus, err = _validate_bonus_config({"type": "days", "days": 5, "label": "x", "ttl_days": 14})
    assert err is None and bonus["ttl_days"] == 14


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "other", "label": "x"},  # неподдерживаемый type
        {"type": "promo", "label": "x"},  # promo без code
        {"type": "promo", "code": "ab", "label": "x"},  # code короче 3 символов
        {"type": "promo", "code": "bad space", "label": "x"},  # code с пробелом
        {"type": "days", "label": "x"},  # нет days
        {"type": "days", "days": 2, "label": "x"},  # < min (3)
        {"type": "days", "days": 31, "label": "x"},  # > max (30)
        {"type": "days", "days": 5, "label": "   "},  # пустой label
        {"type": "days", "days": 5, "label": "x", "ttl_days": 0},  # ttl < 1
        {"type": "days", "days": 5, "label": "x", "ttl_days": 366},  # ttl > 365
    ],
)
def test_validate_bonus_config_rejects_invalid(raw):
    bonus, err = _validate_bonus_config(raw)
    assert err is not None and err.status_code == 400
    assert bonus is None


def test_validate_bonus_config_rejects_non_dict():
    _, err = _validate_bonus_config([1, 2, 3])
    assert err is not None and err.status_code == 400


def test_decode_bonus_none_and_empty():
    assert _decode_bonus(None) is None
    assert _decode_bonus("") is None
    assert _decode_bonus("{}") is None


def test_decode_bonus_from_dict_and_str_like_asyncpg():
    import json

    expected = {"type": "days", "days": 5, "label": "Получить", "ttl_days": 30}
    assert _decode_bonus({"type": "days", "days": 5, "label": "Получить"}) == expected
    assert _decode_bonus(json.dumps(expected)) == expected


def test_decode_bonus_ignores_unknown_type_and_bad_shape():
    assert _decode_bonus({"type": "other", "label": "x"}) is None  # неизвестный type
    assert _decode_bonus({"type": "days", "label": "x"}) is None  # days не int
    assert _decode_bonus({"type": "promo", "label": "x"}) is None  # promo без code
    assert _decode_bonus("{not json") is None
    assert _decode_bonus(["not", "a", "dict"]) is None


def test_decode_bonus_tolerates_bad_ttl_without_crash():
    """Битый ttl_days (defense-in-depth) не должен ронять Runner.run()/API — fallback на дефолт."""
    base = {"type": "days", "days": 5, "label": "x"}
    assert _decode_bonus({**base, "ttl_days": "abc"})["ttl_days"] == 30
    assert _decode_bonus({**base, "ttl_days": None})["ttl_days"] == 30
    assert _decode_bonus({**base})["ttl_days"] == 30  # ttl отсутствует — дефолт
    assert _decode_bonus({**base, "ttl_days": 14})["ttl_days"] == 14  # валидный — как есть


# --- Runner: бонус-кнопка в markup + seed claims ----------------------------


_JOB_UUID = "11111111-2222-3333-4444-555555555555"


def test_runner_bonus_appends_days_button_and_seeds_claims():
    """При бонусе в задаче: markup получает бонус-кнопку с bc_days:<job_id>,
    а для каждого получателя сеется eligible-claim (ДО цикла отправки)."""
    client = _FakeClient()
    bonus = {"type": "days", "days": 5, "label": "🎁 Получить 5 дней", "ttl_days": 14}
    runner, pool = _runner(client, [11, 12, 13], bonus=bonus)
    _run(runner.run())

    # Кнопка присутствует в каждом отправленном сообщении (последней строкой).
    expected_cb = _cb(CB_BROADCAST_DAYS, _JOB_UUID)
    assert len(client.markups) == 3
    for markup in client.markups:
        last_row = markup["inline_keyboard"][-1]
        assert last_row == [{"text": "🎁 Получить 5 дней", "callback_data": expected_cb}]

    # Claims посеяны строго для реальных получателей, с правильными днями/ttl.
    assert pool.bonus_seed_rows is not None
    seeded_tg = {row[1] for row in pool.bonus_seed_rows}
    assert seeded_tg == {11, 12, 13}
    # (job_id, tg, days, ttl_days)
    assert all(row[2] == 5 and row[3] == 14 for row in pool.bonus_seed_rows)


def test_runner_bonus_decoded_from_json_string_like_asyncpg():
    """asyncpg без jsonb-кодека отдаёт bonus как str — runner должен json.loads-нуть."""
    import json

    client = _FakeClient()
    bonus_str = json.dumps({"type": "days", "days": 3, "label": "Бонус"})
    runner, pool = _runner(client, [11], bonus=bonus_str)
    _run(runner.run())
    expected_cb = _cb(CB_BROADCAST_DAYS, _JOB_UUID)
    assert client.markups[0]["inline_keyboard"][-1] == [{"text": "Бонус", "callback_data": expected_cb}]
    assert pool.bonus_seed_rows is not None and pool.bonus_seed_rows[0][2] == 3


def test_runner_without_bonus_has_no_days_button_and_no_claims():
    """bonus отсутствует → нет бонус-кнопки, _seed_bonus_claims не вызывался."""
    client = _FakeClient()
    runner, pool = _runner(client, [11, 12])
    _run(runner.run())
    for markup in client.markups:
        # markup=None (нет кнопок) — тоже означает отсутствие бонус-кнопки.
        if markup is None:
            continue
        flat_cbs = [b.get("callback_data") for row in markup["inline_keyboard"] for b in row]
        assert all(not cb.startswith("bc_days:") for cb in flat_cbs if cb)
    assert pool.bonus_seed_rows is None


def test_runner_bonus_seeds_claims_before_send_loop():
    """Инвариант: eligible-claims посеяны ДО первой отправки (получатель не может
    нажать кнопку раньше, чем claim записан). Проверяем число уже отправленных к моменту
    executemany."""
    calls_at_seed: list[int] = []
    client = _FakeClient()

    class _ObservingConn(_FakeConn):
        async def executemany(self, sql, rows):
            calls_at_seed.append(len(client.calls))
            await super().executemany(sql, rows)

    runner, pool = _runner(client, [11, 12, 13], bonus={"type": "days", "days": 3, "label": "x"})
    pool.acquire = lambda: _ObservingConn(pool)  # type: ignore[method-assign]
    _run(runner.run())
    # На момент посева claims отправок ещё не было (seed строго до send-loop).
    assert calls_at_seed == [0]
    assert pool.bonus_seed_rows is not None and len(pool.bonus_seed_rows) == 3


def test_runner_bonus_zero_recipients_seeds_nothing():
    """Пустая аудитория + бонус → claims не сеются, кнопка не отправлялась."""
    client = _FakeClient()
    runner, pool = _runner(client, [], bonus={"type": "days", "days": 3, "label": "x"})
    _run(runner.run())
    assert client.calls == []
    assert pool.bonus_seed_rows is None


# --- Бонус-кнопка рассылки (промокод) ---------------------------------------


def test_validate_bonus_config_promo_happy_path_normalizes_defaults():
    bonus, err = _validate_bonus_config({"type": "promo", "code": "  summer25  ", "label": "  Забрать скидку  "})
    assert err is None
    assert bonus == {"type": "promo", "code": "SUMMER25", "label": "Забрать скидку", "ttl_days": 30}


def test_validate_bonus_config_promo_respects_explicit_ttl():
    bonus, err = _validate_bonus_config({"type": "promo", "code": "SUMMER25", "label": "L", "ttl_days": 60})
    assert err is None
    assert bonus["ttl_days"] == 60


def test_validate_bonus_config_promo_truncates_label():
    long_label = "Й" * 100  # 100 chars, > _BUTTON_LABEL_MAX_LEN (64)
    bonus, err = _validate_bonus_config({"type": "promo", "code": "SUMMER25", "label": long_label})
    assert err is None
    assert len(bonus["label"]) == 64


def test_decode_bonus_promo_from_dict_and_str_like_asyncpg():
    import json

    expected = {"type": "promo", "code": "SUMMER25", "label": "Скидка", "ttl_days": 30}
    assert _decode_bonus({"type": "promo", "code": "SUMMER25", "label": "Скидка"}) == expected
    assert _decode_bonus(json.dumps(expected)) == expected


def test_decode_bonus_promo_tolerates_bad_ttl_without_crash():
    base = {"type": "promo", "code": "X", "label": "L"}
    assert _decode_bonus({**base, "ttl_days": "abc"})["ttl_days"] == 30
    assert _decode_bonus({**base, "ttl_days": None})["ttl_days"] == 30
    assert _decode_bonus({**base})["ttl_days"] == 30
    assert _decode_bonus({**base, "ttl_days": 14})["ttl_days"] == 14


def test_runner_promo_appends_promo_button_and_seeds_claims():
    """При promo-бонусе: markup получает кнопку с bc_promo:<job_id>, а для каждого получателя
    сеется реестр broadcast_promo_claims (ДО цикла отправки)."""
    client = _FakeClient()
    bonus = {"type": "promo", "code": "SUMMER25", "label": "🎟 Забрать скидку", "ttl_days": 14}
    runner, pool = _runner(client, [11, 12, 13], bonus=bonus)
    _run(runner.run())

    expected_cb = _cb(CB_BROADCAST_PROMO, _JOB_UUID)
    assert len(client.markups) == 3
    for markup in client.markups:
        last_row = markup["inline_keyboard"][-1]
        assert last_row == [{"text": "🎟 Забрать скидку", "callback_data": expected_cb}]

    # Реестр посеян строго для реальных получателей: (job_id, tg, code, ttl_days).
    assert pool.bonus_seed_rows is not None
    seeded_tg = {row[1] for row in pool.bonus_seed_rows}
    assert seeded_tg == {11, 12, 13}
    assert all(row[2] == "SUMMER25" and row[3] == 14 for row in pool.bonus_seed_rows)


def test_runner_promo_decoded_from_json_string_like_asyncpg():
    import json

    client = _FakeClient()
    bonus_str = json.dumps({"type": "promo", "code": "X", "label": "Бонус"})
    runner, pool = _runner(client, [11], bonus=bonus_str)
    _run(runner.run())
    expected_cb = _cb(CB_BROADCAST_PROMO, _JOB_UUID)
    assert client.markups[0]["inline_keyboard"][-1] == [{"text": "Бонус", "callback_data": expected_cb}]
    assert pool.bonus_seed_rows is not None and pool.bonus_seed_rows[0][2] == "X"


def test_runner_promo_seeds_claims_before_send_loop():
    """Инвариант: реестр promo-claims посеян ДО первой отправки (как у дней)."""
    calls_at_seed: list[int] = []
    client = _FakeClient()

    class _ObservingConn(_FakeConn):
        async def executemany(self, sql, rows):
            calls_at_seed.append(len(client.calls))
            await super().executemany(sql, rows)

    runner, pool = _runner(client, [11, 12, 13], bonus={"type": "promo", "code": "X", "label": "L"})
    pool.acquire = lambda: _ObservingConn(pool)  # type: ignore[method-assign]
    _run(runner.run())
    assert calls_at_seed == [0]
    assert pool.bonus_seed_rows is not None and len(pool.bonus_seed_rows) == 3


def test_runner_promo_zero_recipients_seeds_nothing():
    client = _FakeClient()
    runner, pool = _runner(client, [], bonus={"type": "promo", "code": "X", "label": "L"})
    _run(runner.run())
    assert client.calls == []
    assert pool.bonus_seed_rows is None


# --- _validate_bonus_promo_exists (send/test gate) --------------------------


class _PromoExistsPool:
    """Фейк-пул для _validate_bonus_promo_exists: единственный запрос — SELECT promo_codes."""

    def __init__(self, row: dict | None) -> None:
        self._row = row

    async def fetchrow(self, sql: str, *args):
        return self._row


def _promo_row(
    *,
    audience: str = "all_users",
    is_active: bool = True,
    expires_at=None,
    max_redemptions=None,
    redemptions_count: int = 0,
) -> dict:
    return {
        "audience": audience,
        "is_active": is_active,
        "expires_at": expires_at,
        "max_redemptions": max_redemptions,
        "redemptions_count": redemptions_count,
    }


def test_validate_bonus_promo_exists_skips_when_no_promo_bonus():
    pool = _PromoExistsPool(_promo_row())  # не должно вызываться
    # bonus=None и type!="promo" → (None, []) без обращения к БД.
    assert _run(_validate_bonus_promo_exists(pool, None)) == (None, [])
    assert _run(_validate_bonus_promo_exists(pool, {"type": "days", "days": 3, "label": "x"})) == (None, [])


def test_validate_bonus_promo_exists_not_found_or_inactive():
    pool_missing = _PromoExistsPool(None)
    err, warnings = _run(_validate_bonus_promo_exists(pool_missing, {"type": "promo", "code": "X", "ttl_days": 30}))
    assert err is not None and err.status_code == 400 and warnings == []

    pool_inactive = _PromoExistsPool(_promo_row(is_active=False))
    err, warnings = _run(_validate_bonus_promo_exists(pool_inactive, {"type": "promo", "code": "X", "ttl_days": 30}))
    assert err is not None and err.status_code == 400 and warnings == []


def test_validate_bonus_promo_exists_expired():
    from datetime import UTC, datetime, timedelta

    pool = _PromoExistsPool(_promo_row(expires_at=datetime.now(UTC) - timedelta(days=1)))
    err, warnings = _run(_validate_bonus_promo_exists(pool, {"type": "promo", "code": "X", "ttl_days": 30}))
    assert err is not None and err.status_code == 400 and warnings == []


def test_validate_bonus_promo_exists_exhausted():
    pool = _PromoExistsPool(_promo_row(max_redemptions=10, redemptions_count=10))
    err, warnings = _run(_validate_bonus_promo_exists(pool, {"type": "promo", "code": "X", "ttl_days": 30}))
    assert err is not None and err.status_code == 400 and warnings == []


def test_validate_bonus_promo_exists_ok_no_warnings():
    """Валидный all_users промокод без срока → нет ошибки, нет предупреждений."""
    pool = _PromoExistsPool(_promo_row())  # all_users, expires_at=None
    err, warnings = _run(_validate_bonus_promo_exists(pool, {"type": "promo", "code": "X", "ttl_days": 30}))
    assert err is None and warnings == []


def test_validate_bonus_promo_exists_warns_on_audience_mismatch():
    """audience != all_users → non-blocking warning (часть получателей увидит «недоступен»)."""
    pool = _PromoExistsPool(_promo_row(audience="new_users"))
    err, warnings = _run(_validate_bonus_promo_exists(pool, {"type": "promo", "code": "X", "ttl_days": 30}))
    assert err is None
    assert len(warnings) == 1
    assert "new_users" in warnings[0]


def test_validate_bonus_promo_exists_warns_on_expires_within_ttl():
    """promo.expires_at в пределах ttl кнопки → warning о поздних нажатиях."""
    from datetime import UTC, datetime, timedelta

    expires_soon = datetime.now(UTC) + timedelta(days=5)
    pool = _PromoExistsPool(_promo_row(expires_at=expires_soon))
    err, warnings = _run(_validate_bonus_promo_exists(pool, {"type": "promo", "code": "X", "ttl_days": 30}))
    assert err is None
    assert len(warnings) == 1
    assert "истекает" in warnings[0]


def test_validate_bonus_promo_exists_no_warning_when_expires_far():
    """promo.expires_at далеко за пределами ttl кнопки → warning не нужен."""
    from datetime import UTC, datetime, timedelta

    expires_far = datetime.now(UTC) + timedelta(days=100)
    pool = _PromoExistsPool(_promo_row(expires_at=expires_far))
    err, warnings = _run(_validate_bonus_promo_exists(pool, {"type": "promo", "code": "X", "ttl_days": 30}))
    assert err is None and warnings == []


# --- Runner: веб-канал fan-out ----------------------------------------------


def test_runner_web_fanout_inserts_notifications_and_seeds_webonly_claims():
    """Смешанная аудитория: TG (11) + web-only (-5) + линкнутый (11 в обоих каналах).
    Web-канал: одно INSERT-множество на всю web-аудиторию; claims для web-only (tg<=0);
    у линкнутого claim уже сидирован TG-этапом — дубля нет (общий PK)."""
    client = _FakeClient()
    bonus = {"type": "days", "days": 5, "label": "🎁 Получить 5 дней", "ttl_days": 14}
    runner, pool = _runner(client, [11], bonus=bonus, web_recipients=[-5, 11])
    _run(runner.run())

    # Одно множественное INSERT на web-аудиторию, kind/title/payload корректны.
    assert len(pool.web_notification_inserts) == 1
    inserted = pool.web_notification_inserts[0]
    assert set(inserted["tg_ids"]) == {-5, 11}
    assert inserted["title"] == "hi"  # plain_title("<b>hi</b>")
    assert inserted["body"] == "<b>hi</b>"
    import json as _json

    payload = _json.loads(inserted["payload"])
    assert payload["job_id"] == _JOB_UUID
    assert payload["bonus"]["type"] == "days" and payload["bonus"]["days"] == 5

    # Claims: TG-этап сеет 11, web-этап — только web_only (-5). Линкнутый 11 — один раз.
    seeded = {row[1] for row in (pool.bonus_seed_rows or [])}
    assert seeded == {11, -5}
    assert sum(1 for row in (pool.bonus_seed_rows or []) if row[1] == 11) == 1

    # Счётчики web-канала записаны.
    assert pool.web_recipient_count_written == 2
    assert pool.web_notified_count_written == 2


def test_runner_web_only_broadcast_not_killed_by_early_return():
    """Регресс: TG-получателей нет, но web-аудитория есть → рассылка НЕ выходит по
    раннему return, job завершается completed, клейм сидирован для web-only."""
    client = _FakeClient()
    bonus = {"type": "days", "days": 3, "label": "x"}
    runner, pool = _runner(client, [], bonus=bonus, web_recipients=[-7])
    _run(runner.run())
    assert pool.finish[0] == "completed"
    assert client.calls == []  # TG-отправок не было
    assert pool.total_recipients_written == 0
    assert {row[1] for row in (pool.bonus_seed_rows or [])} == {-7}
    assert pool.web_notification_inserts and pool.web_notification_inserts[0]["tg_ids"] == [-7]


def test_runner_web_fanout_failure_does_not_break_tg_cycle():
    """Сбой веб-канала (INSERT упал) — логируется, но TG-цикл доезжает до completed."""
    client = _FakeClient()
    runner, pool = _runner(client, [11, 12], web_recipients=[-5])
    pool.fail_web_insert = True
    _run(runner.run())
    assert pool.finish[0] == "completed"
    assert pool.finish[1] == 2  # оба TG-получателя получили
    assert client.calls == [11, 12]
    assert pool.web_notification_inserts == []


def test_runner_web_fanout_uses_all_explicit_ids():
    """Явные цели: web-каналу уходят ВСЕ id (включая отрицательные web-only),
    TG-каналу — только положительные."""
    client = _FakeClient()
    runner, pool = _runner(client, [11], explicit_ids=[11, -42], web_recipients=[11, -42])
    _run(runner.run())
    # TG-отправка только положительному 11; web-вставка — обоим.
    assert client.calls == [11]
    assert set(pool.web_notification_inserts[0]["tg_ids"]) == {11, -42}
