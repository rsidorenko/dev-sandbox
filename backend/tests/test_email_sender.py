"""Tests for the subscription-link email sender: message structure and SMTP send.

No real network: smtplib.SMTP is mocked. We assert the multipart shape,
the 'Bravada' From-name, the copyable magic-link in both parts, and the
deliverability headers.
"""

from unittest.mock import MagicMock

import pytest

from app.email import sender

_LINK_URL = "https://bravada-connect.ru/auth/test-subscription-token"


@pytest.fixture
def smtp_config_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.yandex.ru")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "noreply@bravada-connect.ru")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM", "noreply@bravada-connect.ru")
    monkeypatch.setenv("SMTP_USE_TLS", "1")


def _build_msg(link_url: str = _LINK_URL, email: str = "user@example.com"):
    config = sender.SmtpConfig(
        host="smtp.yandex.ru",
        port=587,
        user="noreply@bravada-connect.ru",
        password="secret",
        from_address="noreply@bravada-connect.ru",
        use_tls=True,
    )
    return sender._build_link_message(config, email, link_url)


def _walk_payload(msg, content_type: str):
    """First sub-part of `content_type` anywhere in the MIME tree (walks related/alternative)."""
    for part in msg.walk():
        if part.get_content_type() == content_type:
            return part
    return None


def test_message_structure_with_inline_logo(monkeypatch):
    # Deterministic: stub the logo loader so the structure test does not depend on
    # the binary asset being present at runtime.
    monkeypatch.setattr(sender, "_load_logo_bytes", lambda: b"\xff\xd8\xff\xe0fake-jpeg")
    msg = _build_msg()
    assert msg.get_content_type() == "multipart/related"
    types = [p.get_content_type() for p in msg.walk()]
    assert "multipart/alternative" in types
    assert "text/plain" in types
    assert "text/html" in types
    assert "image/jpeg" in types
    logo = next(p for p in msg.walk() if p.get_content_type() == "image/jpeg")
    assert logo["Content-ID"] == "<logo>"
    html = _walk_payload(msg, "text/html").get_payload(decode=True).decode("utf-8")
    assert "cid:logo" in html


def test_message_falls_back_without_logo(monkeypatch):
    # Missing asset → envelope degrades to multipart/alternative, HTML has no broken cid: img.
    monkeypatch.setattr(sender, "_load_logo_bytes", lambda: None)
    msg = _build_msg()
    assert msg.get_content_type() == "multipart/alternative"
    html = _walk_payload(msg, "text/html").get_payload(decode=True).decode("utf-8")
    assert "cid:logo" not in html


def test_message_subject_and_recipient():
    msg = _build_msg(email="user@example.com")
    assert msg["Subject"] == "Ваша ссылка для входа в Bravada"
    assert msg["To"] == "user@example.com"


def test_from_has_bravada_display_name():
    raw_from = _build_msg()["From"]
    assert "Bravada" in raw_from
    assert "noreply@bravada-connect.ru" in raw_from


def test_has_deliverability_headers():
    msg = _build_msg()
    assert msg["Message-ID"]
    assert "bravada-connect.ru" in msg["Message-ID"]
    assert msg["Date"]
    assert msg["Reply-To"] == "noreply@bravada-connect.ru"
    assert msg["List-Unsubscribe"]


def test_parts_contain_copyable_link():
    # Ссылка — сам креденшл: обязана целиком присутствовать в plain-части
    # (копируемой) и в html (кнопка + дублирующая ссылка).
    msg = _build_msg(link_url=_LINK_URL)
    plain = _walk_payload(msg, "text/plain").get_payload(decode=True).decode("utf-8")
    html = _walk_payload(msg, "text/html").get_payload(decode=True).decode("utf-8")
    assert _LINK_URL in plain
    assert _LINK_URL in html
    # Запасной канал восстановления для пользователя без Bot-контекста.
    assert "бессрочная" in plain


def test_no_otp_code_surface():
    # OTP-флоу удалён (миграция 098): в письме не должно остаться код-шаблонов.
    msg = _build_msg()
    plain = _walk_payload(msg, "text/plain").get_payload(decode=True).decode("utf-8")
    html = _walk_payload(msg, "text/html").get_payload(decode=True).decode("utf-8")
    assert "Код подтверждения" not in plain
    assert "Код подтверждения" not in html
    assert "Ваш код:" not in plain


async def test_send_returns_false_without_config(monkeypatch):
    # No SMTP env → config is None → returns False without touching the network.
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    result = await sender.send_subscription_link("user@example.com", _LINK_URL)
    assert result is False


async def test_send_success_with_mocked_smtp(smtp_config_env, monkeypatch):
    fake_server = MagicMock()
    monkeypatch.setattr(sender.smtplib, "SMTP", MagicMock(return_value=fake_server))

    result = await sender.send_subscription_link("user@example.com", _LINK_URL)
    assert result is True
    fake_server.starttls.assert_called_once()
    fake_server.login.assert_called_once_with("noreply@bravada-connect.ru", "secret")
    fake_server.sendmail.assert_called_once()
    # sendmail(from_addr, to_addrs, message) — verify envelope + that the message
    # really carries the link (body parts are base64 on the wire → parse & decode).
    from_addr, to_addrs, blob = fake_server.sendmail.call_args[0]
    assert from_addr == "noreply@bravada-connect.ru"
    assert to_addrs == ["user@example.com"]
    import email as email_lib

    parsed = email_lib.message_from_string(blob)
    plain = next(
        p.get_payload(decode=True).decode("utf-8") for p in parsed.walk() if p.get_content_type() == "text/plain"
    )
    assert _LINK_URL in plain
    fake_server.quit.assert_called_once()


async def test_send_returns_false_on_smtp_error(smtp_config_env, monkeypatch):
    fake_server = MagicMock()
    fake_server.login.side_effect = Exception("auth failed")
    monkeypatch.setattr(sender.smtplib, "SMTP", lambda *a, **k: fake_server)
    result = await sender.send_subscription_link("user@example.com", _LINK_URL)
    assert result is False
