"""SMTP email sender for verification codes (Yandex Mail for Domain)."""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText

_LOGGER = logging.getLogger(__name__)

ENV_SMTP_HOST = "SMTP_HOST"
ENV_SMTP_PORT = "SMTP_PORT"
ENV_SMTP_USER = "SMTP_USER"
ENV_SMTP_PASSWORD = "SMTP_PASSWORD"
ENV_SMTP_FROM = "SMTP_FROM"
ENV_SMTP_USE_TLS = "SMTP_USE_TLS"

_DEFAULT_SMTP_PORT = 587
_DEFAULT_SMTP_USE_TLS = True


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


_VERIFICATION_EMAIL_SUBJECT = "Код подтверждения"
_VERIFICATION_EMAIL_BODY = (
    "Ваш код подтверждения: {code}\n\n"
    "Код действителен 10 минут.\n"
    "Если вы не запрашивали код, проигнорируйте это письмо."
)


async def send_verification_code(email: str, code: str) -> bool:
    config = load_smtp_config()
    if config is None:
        _LOGGER.warning("email.sender.smtp_not_configured")
        return False
    body = _VERIFICATION_EMAIL_BODY.format(code=code)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = _VERIFICATION_EMAIL_SUBJECT
    msg["From"] = config.from_address
    msg["To"] = email
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
