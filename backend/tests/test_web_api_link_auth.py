"""Тесты link-auth эндпоинта POST /api/v1/auth/link/request.

Ключевые инварианты (link-auth дизайн, 2026-09-20):
- web-identity создаётся сразу (отрицательный tg-id, ``web_``-префикс без email-хеша),
  токен — немедленно (ссылка обязана работать до оплаты);
- почта НЕ сохраняется: ни одного SQL с ``user_emails`` и ни одного параметра- email;
- одноразовые домены / отсутствие SMTP / per-IP rate limit отсекаются ДО создания аккаунта;
- реферальный код атрибутируется на новый аккаунт.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

import app.web_api.link_auth as link_auth
from app.web_api.app import build_web_api_app

_SENT_LINKS: list[tuple[str, str]] = []


class _LinkAuthFakePool:
    """asyncpg-подобный пул: последовательность существует, identity пустая,
    все SQL и параметры записываются для аудита «почта не сохраняется»."""

    def __init__(self) -> None:
        self.sql_log: list[tuple[str, tuple[Any, ...]]] = []
        self._next_web_id = -42

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.sql_log.append((sql, args))
        if "information_schema.sequences" in sql:
            return 1
        if "nextval" in sql:
            value = self._next_web_id
            self._next_web_id -= 1
            return value
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        self.sql_log.append((sql, args))
        if "SELECT subscription_token" in sql:
            return {"subscription_token": None}
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def execute(self, sql: str, *args: Any) -> str:
        self.sql_log.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self.sql_log.append((sql, args))
        return []


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _LinkAuthFakePool]:
    # не подключаем реальную инфраструктуру (XUI / Telegram) при сборке приложения
    for var in ("XUI_ENABLED", "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    # SMTP «настроен», отправка письма — мок с записью (email, link_url)
    monkeypatch.setattr(link_auth, "load_smtp_config", lambda: object())
    _SENT_LINKS.clear()

    async def _fake_send(email: str, link_url: str) -> bool:
        _SENT_LINKS.append((email, link_url))
        return True

    monkeypatch.setattr(link_auth, "send_subscription_link", _fake_send)

    # изолированный rate-limit между тестами
    link_auth._rate_limit_store.clear()

    pool = _LinkAuthFakePool()
    app = build_web_api_app(pool=pool)  # type: ignore[arg-type]
    return TestClient(app), pool


def _post(client: TestClient, **payload: Any):
    return client.post("/api/v1/auth/link/request", json=payload)


def test_link_request_creates_identity_and_sends_link(client) -> None:
    test_client, pool = client
    r = _post(test_client, email="user@gmail.com")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "sent": True}

    # identity создана с отрицательным id и web_-префиксом (без email-хеша)
    inserts = [entry for entry in pool.sql_log if "INSERT INTO user_identities" in entry[0]]
    assert len(inserts) == 1
    tg_id, internal_id = inserts[0][1]
    assert tg_id < 0
    assert internal_id.startswith("web_")
    assert "sha" not in internal_id and len(internal_id) == len("web_") + 12

    # токен сгенерирован немедленно и попал в ссылку письма
    token_updates = [entry for entry in pool.sql_log if "SET subscription_token" in entry[0]]
    assert len(token_updates) == 1
    token = token_updates[0][1][0]
    assert len(_SENT_LINKS) == 1
    email, link = _SENT_LINKS[0]
    assert email == "user@gmail.com"
    assert link.endswith(f"/auth/{token}")


def test_link_request_never_persists_email(client) -> None:
    test_client, pool = client
    _post(test_client, email="secret.address@gmail.com")

    # ни одного обращения к таблицам почт и ни одного параметра-почты
    for sql, args in pool.sql_log:
        assert "user_emails" not in sql, sql
        assert "email_verification_codes" not in sql, sql
        for arg in args:
            assert "secret.address" not in str(arg), sql


def test_link_request_rejects_disposable_email(client) -> None:
    test_client, pool = client
    r = _post(test_client, email="spammer@mailinator.com")
    assert r.status_code == 422
    assert r.json()["error"] == "disposable_email"
    # аккаунт не создан
    assert not [entry for entry in pool.sql_log if "INSERT INTO user_identities" in entry[0]]
    assert _SENT_LINKS == []


def test_link_request_requires_smtp(client, monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, pool = client
    monkeypatch.setattr(link_auth, "load_smtp_config", lambda: None)
    r = _post(test_client, email="user@gmail.com")
    assert r.status_code == 503
    assert r.json()["error"] == "email_not_configured"
    assert not [entry for entry in pool.sql_log if "INSERT INTO user_identities" in entry[0]]


def test_link_request_rate_limited_per_ip(client) -> None:
    test_client, _pool = client
    for i in range(5):
        r = _post(test_client, email=f"user{i}@gmail.com")
        assert r.status_code == 200
    r6 = _post(test_client, email="sixth@gmail.com")
    assert r6.status_code == 429
    assert r6.json()["error"] == "rate_limited"
    # шестое письмо не ушло
    assert len(_SENT_LINKS) == 5


def test_link_request_invalid_email(client) -> None:
    test_client, _pool = client
    r = _post(test_client, email="not-an-email")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_email"


def test_link_request_applies_referral(client, monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, pool = client
    calls: list[dict[str, Any]] = []

    from app.application import referral_handler

    async def _fake_apply(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(referral_handler, "apply_referral_on_registration", _fake_apply)

    r = _post(test_client, email="referred@gmail.com", referral_code="abc123")
    assert r.status_code == 200
    assert len(calls) == 1
    assert calls[0]["referral_code"] == "abc123"
    assert str(calls[0]["new_internal_user_id"]).startswith("web_")


def test_link_request_next_path_sanitized(client) -> None:
    test_client, _pool = client
    # валидный внутренний путь попадает в ссылку
    _post(test_client, email="a1@gmail.com", next="/payment/1m")
    assert "next=%2Fpayment%2F1m" in _SENT_LINKS[0][1]

    # внешний URL/схема — игнорируются (open-redirect в письме невозможен)
    _post(test_client, email="a2@gmail.com", next="https://evil.example/phish")
    assert "next=" not in _SENT_LINKS[1][1]

    _post(test_client, email="a3@gmail.com", next="//evil.example")
    assert "next=" not in _SENT_LINKS[2][1]
