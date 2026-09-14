"""Тесты веб-канала NotificationScheduler (двухканальный _send_notification).

Юниты — на фейковом пуле/HTTP (без БД и сети): web-only получает
web_notification + дедуп-запись без TG-вызова; линкнутый — оба канала и ОДНА
запись notification_log; TG-only — как раньше; сбой TG при наличии email не
мешает веб-доставке. Контракт ``_WEB_KINDS`` ⊆ CHECK миграции 077.

Интеграционная часть (opt-in DATABASE_URL, реальный PG): снятие ``> 0`` с
referral-запросов — web-only с verified email получает referral_offer_trial
веб-уведомление.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from app.domain.web_notifications import WEB_NOTIFICATION_KINDS
from app.runtime.notification_scheduler import _WEB_KINDS, NotificationScheduler

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

_WEB_TG = -9_100_000_001
_LINKED_TG = 9_100_000_002
_INTERNAL = "sched_web_test_user"
_FORBIDDEN_TG = 9_100_000_003
_FORBIDDEN_INTERNAL = "sched_tg_forbidden_test_user"


# --- Фейки --------------------------------------------------------------------


class _FakeConn:
    """Соединение для PostgresWebNotificationRepository.insert_one (через acquire)."""

    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetchrow(self, sql: str, *args):
        if "INSERT INTO web_notifications" in sql:
            if self._pool.web_failure:
                raise RuntimeError("temporary web failure")
            self._pool.web_inserts.append({"sql": sql, "args": args})
            return {"id": 1}
        return None


class _FakePool:
    """Скриптованный пул: идентичность (tg_id + has_email), дедуп, лог-вставки."""

    def __init__(
        self,
        *,
        tg_id: int,
        has_email: bool,
        dedup: bool = False,
        identity_missing: bool = False,
        web_failure: bool = False,
    ) -> None:
        self.tg_id = tg_id
        self.has_email = has_email
        self.dedup = dedup
        self.identity_missing = identity_missing
        self.web_failure = web_failure
        self.web_inserts: list[dict] = []
        self.log_inserts: list[tuple] = []

    def acquire(self):
        return _FakeConn(self)

    async def fetchrow(self, sql: str, *args):
        if "FROM user_identities" in sql:
            return None if self.identity_missing else {"telegram_user_id": self.tg_id, "has_email": self.has_email}
        return None

    async def fetchval(self, sql: str, *args):
        if "notification_log" in sql and "sent_date" in sql:
            return 1 if self.dedup else None
        return None

    async def execute(self, sql: str, *args):
        if "INSERT INTO notification_log" in sql:
            self.log_inserts.append(args)
        return "INSERT 0 1"


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeHttp:
    """HTTP-клиент TG: помнит вызовы, возвращает заданный статус."""

    is_closed = False  # _get_http_client проверяет флаг перед реюзом

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[int] = []  # chat_id

    async def post(self, _url: str, json: dict):
        self.calls.append(int(json["chat_id"]))
        return _FakeResponse(self.status_code)


class _StubProvider:
    """VlessProviderPort-заглушка (не используется в этих тестах)."""

    async def revoke_user(self, *, internal_user_id: str):
        raise NotImplementedError

    async def delete_user(self, *, internal_user_id: str):
        raise NotImplementedError


def _scheduler(pool: _FakePool, http: _FakeHttp) -> NotificationScheduler:
    sched = NotificationScheduler(pool=pool, bot_token="test-token", vless_provider=_StubProvider())
    sched._http_client = http  # прямая инъекция: _get_http_client вернёт фейк
    return sched


def _send(sched, pool, notification_type="trial_expiring"):
    return asyncio.run(
        sched._send_notification(
            internal_user_id=_INTERNAL,
            notification_type=notification_type,
            text="Тестовое уведомление",
            keyboard={"inline_keyboard": []},
        )
    )


# --- Юниты --------------------------------------------------------------------


def test_web_kind_map_is_subset_of_migration_check():
    """Контракт: kind'ы веб-канала ⊆ множества CHECK миграции 077."""
    assert {kind for kind, _title in _WEB_KINDS.values()} <= WEB_NOTIFICATION_KINDS


def test_web_only_user_gets_web_notification_without_tg_call():
    """Web-only (tg<0, email): веб-вставка + дедуп-запись; TG НЕ зовётся
    (раньше — ежечасный 400 от Bot API)."""
    pool = _FakePool(tg_id=_WEB_TG, has_email=True)
    http = _FakeHttp()
    assert _send(_scheduler(pool, http), pool) is True
    assert len(pool.web_inserts) == 1
    # insert_one: args = (internal_user_id, kind, title, body, payload_json)
    args = pool.web_inserts[0]["args"]
    assert args[0] == _INTERNAL
    assert args[1] == "trial_expiring"
    assert args[2] == "Триал заканчивается"
    assert json.loads(args[4]) == {"notification_type": "trial_expiring"}
    assert http.calls == []  # TG не дёргался
    assert pool.log_inserts == [(_INTERNAL, "trial_expiring", "delivered")]


def test_non_broadcast_web_notification_does_not_expose_telegram_html() -> None:
    pool = _FakePool(tg_id=_WEB_TG, has_email=True)
    http = _FakeHttp()
    scheduler = _scheduler(pool, http)

    result = asyncio.run(
        scheduler._send_notification(
            internal_user_id=_INTERNAL,
            notification_type="trial_expiring",
            text="⏳ <b>Доступ до 15.09</b>",
            keyboard={"inline_keyboard": []},
        )
    )

    assert result is True
    assert pool.web_inserts[0]["args"][3] == "⏳ Доступ до 15.09"


def test_linked_user_gets_both_channels_and_single_log_row():
    pool = _FakePool(tg_id=_LINKED_TG, has_email=True)
    http = _FakeHttp()
    assert _send(_scheduler(pool, http), pool) is True
    assert http.calls == [_LINKED_TG]
    assert len(pool.web_inserts) == 1
    assert len(pool.log_inserts) == 1  # одна запись на оба канала


def test_tg_only_user_unchanged_behavior():
    pool = _FakePool(tg_id=_LINKED_TG, has_email=False)
    http = _FakeHttp()
    assert _send(_scheduler(pool, http), pool) is True
    assert http.calls == [_LINKED_TG]
    assert pool.web_inserts == []
    assert len(pool.log_inserts) == 1


def test_tg_failure_with_email_still_delivers_via_web():
    """TG упал (400), но email есть → веб-доставка считается успехом, дедуп пишется."""
    pool = _FakePool(tg_id=_LINKED_TG, has_email=True)
    http = _FakeHttp(status_code=400)
    assert _send(_scheduler(pool, http), pool) is True
    assert len(pool.web_inserts) == 1
    assert len(pool.log_inserts) == 1


def test_tg_failure_without_email_no_log_retry_next_tick():
    pool = _FakePool(tg_id=_LINKED_TG, has_email=False)
    http = _FakeHttp(status_code=400)
    assert _send(_scheduler(pool, http), pool) is False
    assert pool.web_inserts == []
    assert pool.log_inserts == []  # без лога → ретрай следующим тиком


def test_tg_forbidden_without_email_records_terminal_failure() -> None:
    pool = _FakePool(tg_id=_LINKED_TG, has_email=False)
    http = _FakeHttp(status_code=403)

    assert _send(_scheduler(pool, http), pool) is False
    assert pool.web_inserts == []
    assert pool.log_inserts == [(_INTERNAL, "trial_expiring", "terminal_failure")]


def test_tg_forbidden_with_successful_web_is_delivered() -> None:
    pool = _FakePool(tg_id=_LINKED_TG, has_email=True)
    http = _FakeHttp(status_code=403)

    assert _send(_scheduler(pool, http), pool) is True
    assert len(pool.web_inserts) == 1
    assert pool.log_inserts == [(_INTERNAL, "trial_expiring", "delivered")]


def test_tg_forbidden_does_not_hide_retryable_web_failure() -> None:
    pool = _FakePool(tg_id=_LINKED_TG, has_email=True, web_failure=True)
    http = _FakeHttp(status_code=403)

    assert _send(_scheduler(pool, http), pool) is False
    assert pool.web_inserts == []
    assert pool.log_inserts == []


def test_dedup_skips_both_channels():
    pool = _FakePool(tg_id=_LINKED_TG, has_email=True, dedup=True)
    http = _FakeHttp()
    assert _send(_scheduler(pool, http), pool) is False
    assert http.calls == []
    assert pool.web_inserts == []
    assert pool.log_inserts == []


def test_missing_identity_returns_false():
    pool = _FakePool(tg_id=_LINKED_TG, has_email=True, identity_missing=True)
    http = _FakeHttp()
    assert _send(_scheduler(pool, http), pool) is False
    assert http.calls == []


def test_web_only_without_email_never_delivers():
    """tg<0 и без email — доставить некому: False без лога (ретрай)."""
    pool = _FakePool(tg_id=_WEB_TG, has_email=False)
    http = _FakeHttp()
    assert _send(_scheduler(pool, http), pool) is False
    assert http.calls == []  # отрицательный chat_id не уходит в Bot API
    assert pool.log_inserts == []


# --- Интеграция (opt-in DATABASE_URL) -----------------------------------------


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping scheduler web integration tests")
    return url


def test_referral_offer_reaches_web_only_user_via_web_channel(pg_url):
    """Регресс снятия `> 0` с referral-запросов: web-only с verified email получает
    referral_offer_trial веб-уведомлением; TG не зовётся (инжект фейк-HTTP)."""

    async def _run():
        import asyncpg

        from app.persistence.postgres_migrations import apply_postgres_migrations

        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            async with pool.acquire() as c:
                await c.execute("DELETE FROM notification_log WHERE internal_user_id = $1", _INTERNAL)
                await c.execute("DELETE FROM web_notifications WHERE internal_user_id = $1", _INTERNAL)
                await c.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) "
                    "VALUES ($1, $2) ON CONFLICT (telegram_user_id) DO NOTHING",
                    _WEB_TG,
                    _INTERNAL,
                )
                await c.execute(
                    "INSERT INTO user_emails (telegram_user_id, email, is_verified) "
                    "VALUES ($1, 'sched-web-test@example.test', TRUE) ON CONFLICT DO NOTHING",
                    _WEB_TG,
                )
                await c.execute(
                    "INSERT INTO subscription_snapshots "
                    "(internal_user_id, state_label, plan_id, trial_started_at, trial_expires_at, updated_at) "
                    "VALUES ($1, 'active', NULL, NOW() - INTERVAL '1 hour', NOW() + INTERVAL '2 days', NOW()) "
                    "ON CONFLICT (internal_user_id) DO NOTHING",
                    _INTERNAL,
                )
                await c.execute(
                    "INSERT INTO bare_xray_traffic_daily "
                    "(stat_date, server_id, email, uplink_bytes, downlink_bytes) "
                    "VALUES ((NOW() AT TIME ZONE 'Europe/Moscow')::date, 99101, $1, 1, 1) "
                    "ON CONFLICT (stat_date, server_id, email) DO UPDATE SET uplink_bytes = 1, downlink_bytes = 1",
                    f"user-{_INTERNAL[:16]}",
                )

            sched = NotificationScheduler(pool=pool, bot_token="test-token", vless_provider=_StubProvider())
            http = _FakeHttp()
            sched._http_client = http
            from datetime import UTC, datetime

            await sched._check_referral_offer_trial(datetime.now(UTC))

            assert http.calls == []  # web-only — TG-отправки нет
            row = await pool.fetchrow(
                "SELECT kind, title, payload FROM web_notifications WHERE internal_user_id = $1",
                _INTERNAL,
            )
            assert row is not None
            assert row["kind"] == "referral_offer"
            assert json.loads(row["payload"]) == {"notification_type": "referral_offer_trial"}
            logged = await pool.fetchval(
                "SELECT 1 FROM notification_log WHERE internal_user_id = $1 "
                "AND notification_type = 'referral_offer_trial'",
                _INTERNAL,
            )
            assert logged is not None

            # Повторный тик — дедуп: без новой записи
            await sched._check_referral_offer_trial(datetime.now(UTC))
            count = await pool.fetchval(
                "SELECT COUNT(*) FROM web_notifications WHERE internal_user_id = $1",
                _INTERNAL,
            )
            assert int(count or 0) == 1
        finally:
            async with pool.acquire() as c:
                await c.execute(
                    "DELETE FROM bare_xray_traffic_daily WHERE server_id = 99101 AND email = $1",
                    f"user-{_INTERNAL[:16]}",
                )
                await c.execute("DELETE FROM notification_log WHERE internal_user_id = $1", _INTERNAL)
                await c.execute("DELETE FROM web_notifications WHERE internal_user_id = $1", _INTERNAL)
                await c.execute("DELETE FROM user_emails WHERE telegram_user_id = $1", _WEB_TG)
                await c.execute("DELETE FROM subscription_snapshots WHERE internal_user_id = $1", _INTERNAL)
                await c.execute("DELETE FROM user_identities WHERE telegram_user_id = $1", _WEB_TG)
            await pool.close()

    asyncio.run(_run())


def test_tg_forbidden_is_persisted_as_terminal_failure(pg_url) -> None:
    async def _run() -> None:
        import asyncpg

        from app.persistence.postgres_migrations import apply_postgres_migrations

        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM notification_log WHERE internal_user_id = $1", _FORBIDDEN_INTERNAL)
                await conn.execute("DELETE FROM user_identities WHERE telegram_user_id = $1", _FORBIDDEN_TG)
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                    _FORBIDDEN_TG,
                    _FORBIDDEN_INTERNAL,
                )

            scheduler = NotificationScheduler(pool=pool, bot_token="test-token", vless_provider=_StubProvider())
            scheduler._http_client = _FakeHttp(status_code=403)

            assert (
                await scheduler._send_notification(
                    internal_user_id=_FORBIDDEN_INTERNAL,
                    notification_type="trial_expiring_24h",
                    text="Тест",
                    keyboard={"inline_keyboard": []},
                )
                is False
            )
            row = await pool.fetchrow(
                "SELECT delivery_status FROM notification_log "
                "WHERE internal_user_id = $1 AND notification_type = 'trial_expiring_24h'",
                _FORBIDDEN_INTERNAL,
            )
            assert row is not None
            assert row["delivery_status"] == "terminal_failure"

            # Дедуп-запись закрывает следующую попытку без повторного Bot API вызова.
            scheduler._http_client.calls.clear()
            assert (
                await scheduler._send_notification(
                    internal_user_id=_FORBIDDEN_INTERNAL,
                    notification_type="trial_expiring_24h",
                    text="Тест",
                    keyboard={"inline_keyboard": []},
                )
                is False
            )
            assert scheduler._http_client.calls == []
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM notification_log WHERE internal_user_id = $1", _FORBIDDEN_INTERNAL)
                await conn.execute("DELETE FROM user_identities WHERE telegram_user_id = $1", _FORBIDDEN_TG)
            await pool.close()

    asyncio.run(_run())
