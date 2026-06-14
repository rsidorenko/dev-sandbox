"""Tests for disposable/temporary email blocking (web registration gate)."""

from __future__ import annotations

import pytest

from app.security import disposable_email as de


# --- blocklist loads & contains known disposables ---

def test_blocklist_loaded_and_nonempty() -> None:
    de._blocked_domains.cache_clear()
    try:
        blocked = de._blocked_domains()
        assert len(blocked) > 1000
        for d in ("mailinator.com", "temp-mail.org", "10minutemail.com",
                  "guerrillamail.com", "yopmail.com"):
            assert d in blocked
    finally:
        de._blocked_domains.cache_clear()


# --- detection ---

@pytest.mark.parametrize("email", [
    "user@mailinator.com",
    "x@temp-mail.org",
    "y@10minutemail.com",
    "z@guerrillamail.com",
    "w@yopmail.com",
])
def test_disposable_detected(email: str) -> None:
    assert de.is_disposable_email(email) is True


@pytest.mark.parametrize("email", [
    "user@gmail.com",
    "user@yandex.ru",
    "user@mail.ru",
    "user@inbox.ru",
    "user@bk.ru",
    "user@list.ru",
    "user@rambler.ru",
    "user@outlook.com",
    "user@hotmail.com",
    "user@live.com",
    "user@yahoo.com",
    "user@icloud.com",
    "user@proton.me",
    "user@protonmail.com",
    "user@gmx.com",
    "ivan@company-name.ru",      # corporate domain — never disposable
    "name@university.edu",
])
def test_normal_providers_allowed(email: str) -> None:
    assert de.is_disposable_email(email) is False


def test_case_insensitive() -> None:
    assert de.is_disposable_email("User@Mailinator.COM") is True
    assert de.is_disposable_email("USER@GMAIL.COM") is False


def test_subdomain_of_disposable_blocked() -> None:
    # parent-walk: sub.temp-mail.org caught because temp-mail.org is listed
    assert de.is_disposable_email("user@sub.temp-mail.org") is True
    assert de.is_disposable_email("user@a.b.mailinator.com") is True


def test_malformed_fail_open() -> None:
    assert de.is_disposable_email("not-an-email") is False
    assert de.is_disposable_email("a@b") is False           # no dot in domain
    assert de.is_disposable_email("a@@b.com") is False      # double @
    assert de.is_disposable_email("") is False
    assert de.is_disposable_email(None) is False  # type: ignore[arg-type]


# --- fail-open on missing blocklist ---

def test_fail_open_when_blocklist_missing(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(de, "_DATA_FILE", tmp_path / "does-not-exist.txt")
    de._blocked_domains.cache_clear()
    try:
        # even a known disposable is allowed when the list cannot load
        assert de.is_disposable_email("user@mailinator.com") is False
    finally:
        de._blocked_domains.cache_clear()


# --- env allowlist override ---

def test_env_allowlist_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPOSABLE_EMAIL_ALLOW", "temp-mail.org,mailinator.com")
    de._allowlist.cache_clear()
    de._blocked_domains.cache_clear()
    try:
        assert de.is_disposable_email("user@temp-mail.org") is False
        # parent-walk reaches mailinator.com which is now allowlisted
        assert de.is_disposable_email("user@sub.mailinator.com") is False
        # not allowlisted -> still blocked
        assert de.is_disposable_email("user@guerrillamail.com") is True
    finally:
        monkeypatch.delenv("DISPOSABLE_EMAIL_ALLOW", raising=False)
        de._allowlist.cache_clear()


# --- handler wiring: POST /api/v1/auth/email/send-code ---

class _FakePool:
    """Minimal asyncpg-like pool — never reached on the disposable path."""

    async def fetchval(self, *a, **k):  # noqa: ANN001, D401
        return 0

    async def execute(self, *a, **k):  # noqa: ANN001, D401
        return "OK"

    async def fetchrow(self, *a, **k):  # noqa: ANN001, D401
        return None

    async def fetch(self, *a, **k):  # noqa: ANN001, D401
        return []


def test_send_code_rejects_disposable_email(monkeypatch: pytest.MonkeyPatch) -> None:
    # avoid wiring real infra (XUI / telegram client) at app build time
    for var in ("XUI_ENABLED", "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    # make SMTP look unconfigured so a normal email deterministically stops at 503
    # (proves it was NOT blocked as disposable) without sending any mail
    import app.email.sender as sender
    monkeypatch.setattr(sender, "load_smtp_config", lambda: None)

    from starlette.testclient import TestClient

    from app.web_api.app import build_web_api_app

    app = build_web_api_app(pool=_FakePool())  # type: ignore[arg-type]
    client = TestClient(app)

    r = client.post("/api/v1/auth/email/send-code", json={"email": "spammer@mailinator.com"})
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "disposable_email"

    # normal provider passes the disposable gate (-> SMTP-not-configured 503, NOT 422)
    r2 = client.post("/api/v1/auth/email/send-code", json={"email": "user@gmail.com"})
    assert r2.status_code == 503
    assert r2.json()["error"] == "email_not_configured"
