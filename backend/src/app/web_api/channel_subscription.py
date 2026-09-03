"""Строгая проверка подписки перед бесплатными выгодами в Web API."""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.application.trial_channel_gate import channel_username_from_env

_LOGGER = logging.getLogger(__name__)

CHANNEL_SUBSCRIPTION_REQUIRED_ERROR = "channel_subscription_required"


def channel_subscription_required_response() -> JSONResponse:
    """Единый 403 с URL канала для понятного ответа клиенту."""
    return JSONResponse(
        {
            "ok": False,
            "error": CHANNEL_SUBSCRIPTION_REQUIRED_ERROR,
            "channel_url": f"https://t.me/{channel_username_from_env()}",
        },
        status_code=403,
    )


async def require_channel_subscription(request: Request, telegram_user_id: int) -> JSONResponse | None:
    """Вернуть 403, если включённый гейт не подтвердил членство.

    Web-only учётная запись не доказывает подписку: такие ID отрицательные и не
    могут быть переданы в ``getChatMember``. Ошибки Bot API также fail-closed,
    чтобы не выдать подарок в обход условия.
    """
    if not getattr(request.app.state, "channel_subscription_required", False):
        return None
    if telegram_user_id <= 0:
        return channel_subscription_required_response()

    checker = getattr(request.app.state, "channel_membership_checker", None)
    if checker is None:
        _LOGGER.error("channel_subscription_gate.not_configured user_id=%s", telegram_user_id)
        return channel_subscription_required_response()

    try:
        is_member = await checker.is_member(telegram_user_id)
    except Exception:
        _LOGGER.warning("channel_subscription_gate.is_member_raised user_id=%s", telegram_user_id, exc_info=True)
        is_member = None
    if is_member is True:
        return None

    if is_member is None:
        _LOGGER.warning("channel_subscription_gate.check_failed user_id=%s", telegram_user_id)
    return channel_subscription_required_response()
