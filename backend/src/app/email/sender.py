"""SMTP email sender for subscription-link emails (Yandex Mail for Domain).

Единственный продакшн-флоу — magic-link входа (link-auth): письмо несёт
ссылку-подписку и нигде не сохраняется. Коды подтверждения (OTP) удалены
вместе с email-таблицами (миграция 098).
"""

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
_SUBSCRIPTION_LINK_EMAIL_SUBJECT = "Ваша ссылка для входа в Bravada"

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


def _build_link_plain_body(link_url: str) -> str:
    # Magic-link письмо: ссылка — сам креденшл, поэтому она обязана быть в plain-части
    # целиком и копируемой (почтовые клиенты умеют открывать голые URL).
    return (
        "Bravada — ссылка для входа\n\n"
        f"Войти и оплатить подписку:\n{link_url}\n\n"
        "Ссылка бессрочная и персональная: сохраните её — без неё восстановить "
        "доступ невозможно, почта для входа больше не используется.\n"
        "Если вы не запрашивали ссылку, проигнорируйте это письмо."
    )


def _build_link_html_body(link_url: str, with_logo: bool) -> str:
    logo_html = (
        '<img src="cid:logo" alt="Bravada" width="32" height="32"'
        ' style="display:inline-block;vertical-align:middle;border-radius:8px;" />'
        if with_logo
        else ""
    )
    # Кнопка-CTA на /auth/<token> + дублирующая копируемая ссылка (не все клиенты
    # рендерят кнопки). Это magic-link письмо — ссылка и есть действие, а не
    # «код + CTA на /login» (тот паттерн трипал Google Safe Browsing у
    # код-подтверждений; OTP-флоу удалён вместе с email-таблицами).
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
          <h1 style="margin:0 0 8px;font-size:20px;font-weight:700;color:#111827;">Ссылка для входа</h1>
          <p style="margin:0 0 24px;font-size:15px;line-height:22px;color:#4b5563;">Нажмите кнопку, чтобы войти, выбрать тариф и оплатить подписку:</p>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding-bottom:20px;">
            <a href="{link_url}" style="display:inline-block;background-color:#2563eb;color:#ffffff;text-decoration:none;font-size:16px;font-weight:700;padding:14px 36px;border-radius:12px;">Войти в Bravada</a>
          </td></tr></table>
          <p style="margin:0 0 8px;font-size:14px;line-height:20px;color:#4b5563;">Или скопируйте ссылку:</p>
          <div style="background-color:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;padding:14px 16px;text-align:center;">
            <span style="font-size:13px;line-height:18px;color:#1d4ed8;word-break:break-all;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">{link_url}</span>
          </div>
          <p style="margin:24px 0 0;font-size:14px;line-height:20px;color:#6b7280;">Ссылка бессрочная и персональная. Сохраните это письмо: без ссылки восстановить доступ невозможно, вход по почте больше не используется.</p>
          <p style="margin:8px 0 0;font-size:13px;line-height:18px;color:#9ca3af;">Если вы не запрашивали ссылку, просто проигнорируйте это письмо.</p>
        </td></tr>
        <tr><td style="padding:16px 32px 24px;background-color:#f9fafb;border-top:1px solid #f3f4f6;">
          <p style="margin:0;font-size:12px;line-height:16px;color:#9ca3af;text-align:center;">Bravada · bravada-connect.ru</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _build_link_message(config: SmtpConfig, email: str, link_url: str) -> MIMEMultipart:
    logo_bytes = _load_logo_bytes()
    return _assemble_message(
        config,
        email,
        _SUBSCRIPTION_LINK_EMAIL_SUBJECT,
        _build_link_plain_body(link_url),
        _build_link_html_body(link_url, with_logo=logo_bytes is not None),
        logo_bytes=logo_bytes,
    )


def _assemble_message(
    config: SmtpConfig,
    email: str,
    subject: str,
    plain_body: str,
    html_body: str,
    *,
    logo_bytes: bytes | None,
) -> MIMEMultipart:
    """Сборка MIME-сообщения: multipart/related с инлайн-логотипом либо
    multipart/alternative без него. Общий каркас для кода подтверждения и
    magic-link писем."""
    domain = _domain_from_address(config.from_address)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))

    if logo_bytes is None:
        msg: MIMEMultipart = alt
    else:
        msg = MIMEMultipart("related")
        msg.attach(alt)
        logo = MIMEImage(logo_bytes, _subtype="jpeg")
        logo.add_header("Content-ID", "<logo>")
        logo.add_header("Content-Disposition", "inline", filename="logo.jpeg")
        msg.attach(logo)

    msg["Subject"] = subject
    msg["From"] = formataddr((_SENDER_DISPLAY_NAME, config.from_address))
    msg["To"] = email
    msg["Reply-To"] = config.from_address
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    # Transactional reputation signal (Gmail / mail.ru value a real unsubscribe path).
    msg["List-Unsubscribe"] = f"mailto:{config.from_address}?subject=unsubscribe"
    return msg


async def send_subscription_link(email: str, link_url: str) -> bool:
    """Magic-link письмо: ссылка-подписка как способ входа (link-auth, 2026-09-20)."""
    config = load_smtp_config()
    if config is None:
        _LOGGER.warning("email.sender.smtp_not_configured")
        return False
    msg = _build_link_message(config, email, link_url)
    return await _smtp_send(config, email, msg)


async def _smtp_send(config: SmtpConfig, email: str, msg: MIMEMultipart) -> bool:
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
