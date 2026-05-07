"""UC-05 v1: узкий разрешённый список и сентинели (без парсинга провайдера, без сырых нагрузок)."""

from __future__ import annotations

# Только этот нормализованный тип события может установить подписку в active в v1. Продукт может расширить.
UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED = "subscription_activated"

UC05_ALLOWLISTED_EVENT_TYPES: frozenset[str] = frozenset(
    {UC05_ALLOWLISTED_EVENT_TYPE_SUBSCRIPTION_ACTIVATED}
)

# Хранится в billing_subscription_apply_records.internal_user_id когда ledger-факт не имеет пользователя;
# не является реальным internal_user_id (UUID-style id не используют этот паттерн).
UC05_NO_USER_SENTINEL = "_uc05_no_internal_user_"
