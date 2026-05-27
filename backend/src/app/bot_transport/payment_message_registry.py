"""In-memory registry mapping YooKassa payment_id to Telegram (chat_id, message_id).

Used so the webhook handler can edit the original payment prompt message
to show success/failure status after the user completes payment.
"""

from __future__ import annotations

_registry: dict[str, tuple[int, int]] = {}
_pending_by_user: dict[int, str] = {}


def register_payment_message(payment_id: str, chat_id: int, message_id: int) -> None:
    _registry[payment_id] = (chat_id, message_id)


def pop_payment_message(payment_id: str) -> tuple[int, int] | None:
    return _registry.pop(payment_id, None)


def set_pending_payment_for_user(telegram_user_id: int, payment_id: str) -> None:
    _pending_by_user[telegram_user_id] = payment_id


def pop_pending_payment_for_user(telegram_user_id: int) -> str | None:
    return _pending_by_user.pop(telegram_user_id, None)
