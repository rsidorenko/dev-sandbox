"""Tests for the verification email sender: message structure and SMTP send.

No real network: smtplib.SMTP is mocked. We assert the multipart shape,
the 'Bravada' From-name, the autofill-friendly code line, and the
deliverability headers.
"""

from unittest.mock import MagicMock

import pytest

from app.email import sender


@pytest.fixture
def smtp_config_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.yandex.ru")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "noreply@bravada-connect.ru")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM", "noreply@bravada-connect.ru")
    monkeypatch.setenv("SMTP_USE_TLS", "1")


def _build_msg(code: str = "123456", email: str = "user@example.com"):
    config = sender.SmtpConfig(
        host="smtp.yandex.ru",
        port=587,
        user="noreply@bravada-connect.ru",
        password="secret",
        from_address="noreply@bravada-connect.ru",
        use_tls=True,
    )
    return sender._build_message(config, email, code)


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
    assert msg["Subject"] == "Код подтверждения Bravada"
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


def test_parts_contain_code_and_autofill_line():
    msg = _build_msg(code="654321")
    # utf-8 parts are base64-encoded on the wire → decode before string checks
    plain = _walk_payload(msg, "text/plain").get_payload(decode=True).decode("utf-8")
    html = _walk_payload(msg, "text/html").get_payload(decode=True).decode("utf-8")

    # Code present in both parts
    assert "654321" in plain
    assert "654321" in html
    # The plain line iOS Mail / Android autofill recognise to surface the code
    assert "Ваш код: 654321" in plain


def test_email_has_no_login_cta():
    # The verification email must NOT contain a "Войти в кабинет" button or any login
    # URL: a CTA from a verification email to a /login page is a phishing-kit shape
    # that tripped Google Safe Browsing. The one-time code is the only call to action.
    msg = _build_msg(code="112233", email="user@example.com")
    plain = _walk_payload(msg, "text/plain").get_payload(decode=True).decode("utf-8")
    html = _walk_payload(msg, "text/html").get_payload(decode=True).decode("utf-8")

    # No login CTA in either part.
    assert "Войти в кабинет" not in plain
    assert "Войти в кабинет" not in html
    assert "/login" not in plain
    assert "/login" not in html
    # The one-time code is still the only call to action.
    assert "112233" in plain
    assert "112233" in html


async def test_send_returns_false_without_config(monkeypatch):
    # No SMTP env → config is None → returns False without touching the network.
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    result = await sender.send_verification_code("user@example.com", "123456")
    assert result is False


async def test_send_success_with_mocked_smtp(smtp_config_env, monkeypatch):
    fake_server = MagicMock()
    monkeypatch.setattr(sender.smtplib, "SMTP", MagicMock(return_value=fake_server))

    result = await sender.send_verification_code("user@example.com", "123456")
    assert result is True
    fake_server.starttls.assert_called_once()
    fake_server.login.assert_called_once_with("noreply@bravada-connect.ru", "secret")
    fake_server.sendmail.assert_called_once()
    # sendmail(from_addr, to_addrs, message) — verify envelope + that a message blob passed
    from_addr, to_addrs, _blob = fake_server.sendmail.call_args[0]
    assert from_addr == "noreply@bravada-connect.ru"
    assert to_addrs == ["user@example.com"]
    fake_server.quit.assert_called_once()


async def test_send_returns_false_on_smtp_error(smtp_config_env, monkeypatch):
    fake_server = MagicMock()
    fake_server.login.side_effect = Exception("auth failed")
    monkeypatch.setattr(sender.smtplib, "SMTP", lambda *a, **k: fake_server)
    result = await sender.send_verification_code("user@example.com", "123456")
    assert result is False
