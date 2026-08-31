"""Доменные правила веб-уведомлений (чистые функции, без БД).

kind-значения обязаны совпадать с CHECK-констрейнтом миграции 077_web_notifications.sql
(соответствие проверяется тестом test_web_notifications_store).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

WEB_NOTIFICATION_KINDS: frozenset[str] = frozenset(
    {
        "broadcast",
        "trial_expiring",
        "trial_expired",
        "subscription_expiring",
        "subscription_expired",
        "key_deleted",
        "referral_offer",
    }
)

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"(?i)<br\s*/?>")

_DEFAULT_TITLE = "Сообщение от Bravada VPN"


def plain_title(html: str, *, max_len: int = 80) -> str:
    """Заголовок уведомления рассылки из её HTML-текста: <br> → пробел, остальные
    теги вырезаются (не заменяются пробелом — иначе «мир !»), whitespace схлопнут,
    обрезано до ``max_len`` с многоточием."""
    text = " ".join(_TAG_RE.sub("", _BR_RE.sub(" ", html or "")).split())
    if not text:
        return _DEFAULT_TITLE
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def compose_claim_status(
    *,
    bonus: dict[str, Any] | None,
    days_claim: dict[str, Any] | None = None,
    promo_claim: dict[str, Any] | None = None,
    applied_code: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """Статус бонуса рассылки для отображения кнопки в веб-уведомлении.

    Источник истины — claims-таблицы (сидируются fan-out'ом) и promo_applied.
    Возвращает 'eligible' | 'claimed' | 'expired' | None (бонуса нет / не получатель —
    кнопку не рендерим). ``days_claim``: {status, expires_at}; ``promo_claim``:
    {code, expires_at}; ``applied_code`` — код активного применённого промокода.
    """
    if not bonus:
        return None
    now = now or datetime.now(UTC)
    if bonus.get("type") == "promo":
        if not promo_claim:
            return None
        if applied_code and applied_code == promo_claim.get("code"):
            return "claimed"
        expires_at = promo_claim.get("expires_at")
        if expires_at is not None and expires_at <= now:
            return "expired"
        return "eligible"
    # days
    if not days_claim:
        return None
    if days_claim.get("status") == "claimed":
        return "claimed"
    expires_at = days_claim.get("expires_at")
    if expires_at is None or expires_at <= now:
        return "expired"
    return "eligible"
