"""Web link-auth: выдача ссылки-подписки на введённую почту (почта НЕ сохраняется).

Link-auth дизайн (2026-09-20): subscription_token — единственный креденшл пользователя.
Этот эндпоинт — канал доставки для покупателей без подписки:

- почта валидируется и используется только как адрес доставки письма со ссылкой
  ``/auth/<token>``; в БД она не записывается (восстановления по почте нет by design);
- web-identity создаётся сразу (отрицательный tg-id из ``web_user_id_seq``,
  ``internal_user_id = web_<random>`` — без email-хеша), токен — немедленно,
  чтобы ссылка работала ещё до первой оплаты/выдачи ключей;
- каждый запрос создаёт НОВЫЙ аккаунт: дедупликация по почте невозможна без
  её хранения (принятый компромисс), поэтому единственная защита от потока
  регистраций — per-IP rate limit;
- реферальная атрибуция переехала сюда из email-verify (``apply_referral_on_registration``).
"""

from __future__ import annotations

import logging
import re
import secrets
import time

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.email.sender import load_smtp_config, send_subscription_link
from app.issuance.subscription_token import ensure_subscription_token
from app.security.disposable_email import is_disposable_email
from app.shared.site_url import get_site_base_url
from app.web_api.helpers import safe_json_error, validate_email

_LOGGER = logging.getLogger(__name__)

_MAX_REQUESTS_PER_IP_PER_HOUR = 5
_RATE_LIMIT_WINDOW = 3600
_RATE_LIMIT_MAX_ENTRIES = 10000
_rate_limit_store: dict[str, list[float]] = {}
_rate_limit_last_cleanup = 0.0

# Формат реферального кода — как в старом email-verify (auth.py).
_REF_CODE_RE = re.compile(r"^[a-z0-9]{4,32}$", re.IGNORECASE)

# Разрешаем только внутренние пути для next (защита от open-redirect в письме).
_NEXT_PATH_RE = re.compile(r"^/[a-zA-Z0-9\-._~!$&'()*+,;=:@/%]*$")


def _check_rate_limit(client_ip: str) -> bool:
    """Per-IP in-memory лимит (паттерн subscription.py): аккаунты плодятся,
    почта не хранится — другой дедупликации нет."""
    global _rate_limit_last_cleanup
    now = time.monotonic()
    if now - _rate_limit_last_cleanup > _RATE_LIMIT_WINDOW:
        expired = [k for k, v in _rate_limit_store.items() if not v or now - v[-1] > _RATE_LIMIT_WINDOW]
        for k in expired:
            del _rate_limit_store[k]
        if len(_rate_limit_store) > _RATE_LIMIT_MAX_ENTRIES:
            oldest = sorted(_rate_limit_store, key=lambda k: _rate_limit_store[k][-1] if _rate_limit_store[k] else 0)
            for k in oldest[: len(_rate_limit_store) - _RATE_LIMIT_MAX_ENTRIES]:
                del _rate_limit_store[k]
        _rate_limit_last_cleanup = now
    window = _rate_limit_store.get(client_ip, [])
    window = [t for t in window if now - t < _RATE_LIMIT_WINDOW]
    if len(window) >= _MAX_REQUESTS_PER_IP_PER_HOUR:
        _rate_limit_store[client_ip] = window
        return False
    window.append(now)
    _rate_limit_store[client_ip] = window
    return True


def _sanitize_next_path(raw: str) -> str:
    """Внутренний путь для ?next= в письме: только '/…' без '//'/схемы."""
    raw = (raw or "").strip()
    if not raw or not _NEXT_PATH_RE.match(raw) or raw.startswith("//"):
        return ""
    return raw


async def _next_web_telegram_id(pool: asyncpg.Pool) -> int:
    """Отрицательный id для web-only аккаунта (последовательность создаётся
    лениво — как в старом email-verify)."""
    seq_exists = await pool.fetchval(
        "SELECT COUNT(*) FROM information_schema.sequences WHERE sequence_name = 'web_user_id_seq'"
    )
    if not seq_exists:
        await pool.execute(
            "CREATE SEQUENCE IF NOT EXISTS web_user_id_seq START WITH -1 INCREMENT BY -1 MINVALUE -999999999"
        )
    return int(await pool.fetchval("SELECT nextval('web_user_id_seq')"))  # type: ignore[arg-type]


async def handle_link_request(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request", "Expected JSON body")

    email = str(data.get("email", "")).strip().lower()
    if not validate_email(email):
        return safe_json_error(400, "invalid_email")

    # Одноразовые домены — как в send-code: до траты SMTP-отправки.
    if is_disposable_email(email):
        return safe_json_error(422, "disposable_email", "Disposable email providers are not allowed")

    # SMTP должен быть настроен до создания аккаунта.
    if load_smtp_config() is None:
        return safe_json_error(503, "email_not_configured", "Email service is not available")

    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return safe_json_error(429, "rate_limited", "Too many link requests. Try later.")

    pool: asyncpg.Pool = request.app.state.pool

    web_telegram_id = await _next_web_telegram_id(pool)
    # Без email-хеша: почта нигде не хранится, значит и производные от неё — нет.
    web_internal_id = f"web_{secrets.token_hex(6)}"
    await pool.execute(
        "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        web_telegram_id,
        web_internal_id,
    )

    token = await ensure_subscription_token(pool, web_internal_id)

    next_path = _sanitize_next_path(str(data.get("next", "")))
    link_url = f"{get_site_base_url()}/auth/{token}"
    if next_path:
        from urllib.parse import quote

        link_url += f"?next={quote(next_path, safe='')}"

    # Реферальная атрибуция нового аккаунта (перенос из email-verify).
    referral_code = str(data.get("referral_code", "")).strip()
    if referral_code and not _REF_CODE_RE.match(referral_code):
        referral_code = ""
    if referral_code:
        from app.application.referral_handler import apply_referral_on_registration
        from app.persistence.postgres_referral import (
            PostgresReferralCodeRepository,
            PostgresReferralRelationshipRepository,
        )

        try:
            await apply_referral_on_registration(
                new_internal_user_id=web_internal_id,
                referral_code=referral_code,
                code_repo=PostgresReferralCodeRepository(pool),
                relationship_repo=PostgresReferralRelationshipRepository(pool),
            )
        except Exception:
            _LOGGER.warning("web_api.link_auth.referral_apply_failed email=***")

    sent = await send_subscription_link(email, link_url)
    if not sent:
        _LOGGER.warning("web_api.link_auth.send_failed email=***")

    return JSONResponse({"ok": True, "sent": sent})
