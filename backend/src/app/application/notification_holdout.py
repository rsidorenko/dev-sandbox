"""Holdout уведомлений: детерминированная контрольная группа для nudge-типов.

Пользователь из holdout-бакета не получает уведомление — планировщик пишет
``notification_log.delivery_status='holdout'`` (дедуп и наблюдаемость). Разбиение
стабильно для пары (пользователь, тип): один и тот же человек удерживается от
конкретного уведомления всегда, но не от всех сразу. Операционные и
информационные типы никогда не удерживаются.
"""

from __future__ import annotations

import hashlib
import os

# Только nudge/маркетинговые типы. Операционные (keys_deleted — предупреждение об
# удалении ключей) и базовые информационные (trial_expired, subscription_expired)
# доставляются всегда.
HOLDOUT_ELIGIBLE_TYPES = frozenset(
    {
        "trial_activation_nudge_15m",
        "trial_connection_nudge_15m",
        "checkout_abandoned_30m",
        "trial_expiring_24h",
        "trial_expiring_6h",
        "trial_expired_followup_24h",
        "subscription_expiring_3d",
        "referral_offer_trial",
        "referral_offer_paid",
    }
)

_DEFAULT_PERCENT = 10
_MAX_PERCENT = 50


def notification_holdout_enabled() -> bool:
    return os.environ.get("NOTIFICATION_HOLDOUT_ENABLE", "").strip().lower() in ("1", "true", "yes", "on")


def holdout_percent() -> int:
    """Доля удерживаемых, 1..50 (default 10); мусорное значение — дефолт."""
    raw = os.environ.get("NOTIFICATION_HOLDOUT_PERCENT", "").strip()
    try:
        percent = int(raw)
    except ValueError:
        return _DEFAULT_PERCENT
    return max(1, min(_MAX_PERCENT, percent))


def is_notification_holdout(internal_user_id: str, notification_type: str) -> bool:
    """True — удержать уведомление (не отправлять, записать 'holdout')."""
    if not notification_holdout_enabled():
        return False
    if notification_type not in HOLDOUT_ELIGIBLE_TYPES:
        return False
    digest = hashlib.sha256(f"notification_holdout:{internal_user_id}:{notification_type}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % 100
    return bucket < holdout_percent()
