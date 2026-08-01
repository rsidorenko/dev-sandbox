"""SMTP email sender for verification codes (Yandex Mail for Domain)."""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, getaddresses, make_msgid
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

ENV_SMTP_HOST = "SMTP_HOST"
ENV_SMTP_PORT = "SMTP_PORT"
ENV_SMTP_USER = "SMTP_USER"
ENV_SMTP_PASSWORD = "SMTP_PASSWORD"
ENV_SMTP_FROM = "SMTP_FROM"
ENV_SMTP_USE_TLS = "SMTP_USE_TLS"

_DEFAULT_SMTP_PORT = 587
_DEFAULT_SMTP_USE_TLS = True

# Display name shown in the recipient's inbox ("Bravada <noreply@…>").
_SENDER_DISPLAY_NAME = "Bravada"
_VERIFICATION_EMAIL_SUBJECT = "Код подтверждения Bravada"

# Inline brand logo embedded via Content-ID (multipart/related). sender.py →
# parents[3] = backend/, so this points at backend/media/logo.jpeg.
_LOGO_PATH = Path(__file__).resolve().parents[3] / "media" / "logo.jpeg"


def _load_logo_bytes() -> bytes | None:
    """Read the brand logo once; None if absent (HTML falls back to wordmark only)."""
    try:
        return _LOGO_PATH.read_bytes()
    except Exception:
        _LOGGER.warning("email.sender.logo_missing path=%s", _LOGO_PATH)
        return None


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_address: str
    use_tls: bool


def load_smtp_config() -> SmtpConfig | None:
    host = os.environ.get(ENV_SMTP_HOST, "").strip()
    user = os.environ.get(ENV_SMTP_USER, "").strip()
    password = os.environ.get(ENV_SMTP_PASSWORD, "").strip()
    if not host or not user or not password:
        return None
    port_str = os.environ.get(ENV_SMTP_PORT, str(_DEFAULT_SMTP_PORT)).strip()
    try:
        port = int(port_str)
    except ValueError:
        port = _DEFAULT_SMTP_PORT
    from_address = os.environ.get(ENV_SMTP_FROM, "").strip() or user
    use_tls_raw = os.environ.get(ENV_SMTP_USE_TLS, "1").strip().lower()
    use_tls = use_tls_raw in ("1", "true", "yes")
    return SmtpConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        from_address=from_address,
        use_tls=use_tls,
    )


def _domain_from_address(address: str) -> str:
    """Domain part of an email address (for Message-ID); 'localhost' fallback."""
    parsed = getaddresses([address])
    if parsed:
        _, addr = parsed[0]
        if "@" in addr:
            return addr.rsplit("@", 1)[1].strip().lower() or "localhost"
    return "localhost"


def _build_plain_body(code: str) -> str:
    # Plain, machine-friendly layout. The "Ваш код: 123456" line is the pattern
    # iOS Mail / Android autofill recognise to surface the code above the keyboard.
    # No login link/button: a CTA from a verification email to a /login page is a
    # phishing-kit shape that tripped Google Safe Browsing. The code is the only CTA.
    return (
        "Bravada — код подтверждения\n\n"
        f"Ваш код: {code}\n\n"
        "Код действителен 10 минут.\n"
        "Если вы не запрашивали код, проигнорируйте это письмо."
    )


def _build_html_body(code: str, with_logo: bool) -> str:
    # Inline-only CSS, table-based layout for maximum email-client compatibility
    # (Outlook, mail.ru, Gmail, Apple Mail strip <style> blocks and break flex).
    # The logo is embedded inline via cid:logo (multipart/related); if the file
    # is missing at runtime we fall back to the wordmark only (no broken img).
    logo_html = (
        '<img src="cid:logo" alt="Bravada" width="32" height="32"'
        ' style="display:inline-block;vertical-align:middle;border-radius:8px;" />'
        if with_logo
        else ""
    )
    return f"""\
<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background-color:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;background-color:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
        <tr><td style="background-color:#2563eb;padding:20px 32px;">
          {logo_html}<span style="display:inline-block;vertical-align:middle;margin-left:10px;font-size:20px;font-weight:700;color:#ffffff;letter-spacing:0.5px;">Bravada</span>
        </td></tr>
        <tr><td style="padding:32px;">
          <h1 style="margin:0 0 8px;font-size:20px;font-weight:700;color:#111827;">Код подтверждения</h1>
          <p style="margin:0 0 24px;font-size:15px;line-height:22px;color:#4b5563;">Используйте этот код для входа в личный кабинет:</p>
          <div style="background-color:#eff6ff;border:1px solid #dbeafe;border-radius:12px;padding:20px;text-align:center;">
            <span style="font-size:34px;font-weight:700;letter-spacing:10px;color:#1d4ed8;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">{code}</span>
          </div>
          <p style="margin:24px 0 0;font-size:14px;line-height:20px;color:#6b7280;">Код действителен 10 минут.</p>
          <p style="margin:8px 0 0;font-size:13px;line-height:18px;color:#9ca3af;">Если вы не запрашивали код, просто проигнорируйте это письмо.</p>
        </td></tr>
        <tr><td style="padding:16px 32px 24px;background-color:#f9fafb;border-top:1px solid #f3f4f6;">
          <p style="margin:0;font-size:12px;line-height:16px;color:#9ca3af;text-align:center;">Bravada · bravada-connect.ru</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _build_message(config: SmtpConfig, email: str, code: str) -> MIMEMultipart:
    """Build the verification message — multipart/related when the logo is available
    (inline image via Content-ID), otherwise a plain multipart/alternative."""
    domain = _domain_from_address(config.from_address)

    logo_bytes = _load_logo_bytes()
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(_build_plain_body(code), "plain", "utf-8"))
    alt.attach(MIMEText(_build_html_body(code, with_logo=logo_bytes is not None), "html", "utf-8"))

    if logo_bytes is None:
        msg: MIMEMultipart = alt
    else:
        msg = MIMEMultipart("related")
        msg.attach(alt)
        logo = MIMEImage(logo_bytes, _subtype="jpeg")
        logo.add_header("Content-ID", "<logo>")
        logo.add_header("Content-Disposition", "inline", filename="logo.jpeg")
        msg.attach(logo)

    msg["Subject"] = _VERIFICATION_EMAIL_SUBJECT
    msg["From"] = formataddr((_SENDER_DISPLAY_NAME, config.from_address))
    msg["To"] = email
    msg["Reply-To"] = config.from_address
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    # Transactional reputation signal (Gmail / mail.ru value a real unsubscribe path).
    msg["List-Unsubscribe"] = f"mailto:{config.from_address}?subject=unsubscribe"
    return msg


async def send_verification_code(email: str, code: str) -> bool:
    config = load_smtp_config()
    if config is None:
        _LOGGER.warning("email.sender.smtp_not_configured")
        return False
    msg = _build_message(config, email, code)
    try:
        if config.use_tls:
            server = smtplib.SMTP(config.host, config.port, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=10)
            server.ehlo()
        server.login(config.user, config.password)
        server.sendmail(config.from_address, [email], msg.as_string())
        server.quit()
        _LOGGER.info("email.sender.sent", extra={"structured_fields": {"to": "***"}})
        return True
    except Exception:
        _LOGGER.warning("email.sender.send_failed", exc_info=True)
        return False
