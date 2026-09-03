"""Проверка подписки на Telegram-канал перед выдачей бесплатных выгод.

Проверяет через Bot API ``getChatMember``, состоит ли пользователь в канале
(по умолчанию ``@bravada_vpn``). Фича закрыта флагом
``TRIAL_CHANNEL_SUBSCRIBE_ENABLE`` (по умолчанию off) для безопасного
выката/отката — паттерн ``TELEGRAM_CUSTOM_EMOJI_ENABLE``.

``is_member`` возвращает ``None`` при технической неопределённости (сеть,
роль бота в канале, лимит API). Политику выдачи определяет вызывающий код:
для бесплатных выгод он должен разрешать действие только при ``True``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

import httpx

TRIAL_CHANNEL_SUBSCRIBE_ENABLE = "TRIAL_CHANNEL_SUBSCRIBE_ENABLE"
TRIAL_CHANNEL_USERNAME = "TRIAL_CHANNEL_USERNAME"

#: Канал по умолчанию (без @): брендовый канал, уже используемый ботом.
DEFAULT_CHANNEL_USERNAME = "bravada_vpn"

_DEFAULT_TIMEOUT = httpx.Timeout(10.0)

_LOGGER = logging.getLogger(__name__)


def trial_channel_gate_enabled_from_env(env_getter: Mapping[str, str] | None = None) -> bool:
    """Флаг включён, когда переменная окружения — 1/true/yes (как TELEGRAM_ACCESS_RESEND_ENABLE)."""
    raw = (
        os.environ.get(TRIAL_CHANNEL_SUBSCRIBE_ENABLE, "")
        if env_getter is None
        else env_getter.get(TRIAL_CHANNEL_SUBSCRIBE_ENABLE, "") or ""
    )
    return raw.strip().lower() in ("1", "true", "yes")


def channel_username_from_env(env_getter: Mapping[str, str] | None = None) -> str:
    """Username канала без ведущего @; пустое/отсутствующее значение → дефолт."""
    raw = (
        os.environ.get(TRIAL_CHANNEL_USERNAME, "")
        if env_getter is None
        else env_getter.get(TRIAL_CHANNEL_USERNAME, "") or ""
    )
    username = raw.strip().lstrip("@")
    return username or DEFAULT_CHANNEL_USERNAME


class TelegramChannelMembershipChecker:
    """Живая проверка членства в канале через Bot API ``getChatMember``.

    Самостоятельный httpx-клиент: raw-клиент рантайма недоступен фасаду,
    разделение слоёв сохраняется (паттерн YooKassaClient).
    """

    def __init__(
        self,
        bot_token: str,
        channel_username: str = DEFAULT_CHANNEL_USERNAME,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = f"https://api.telegram.org/bot{bot_token}/"
        self._channel_username = channel_username.lstrip("@")
        self._client = http_client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def is_member(self, user_id: int) -> bool | None:
        """True — состоит в канале; False — точно не состоит; None — проверка не удалась.

        Статусы creator/administrator/member — членство; restricted — по полю is_member;
        left/kicked — не состоит. Любая ошибка (сеть, не-ok ответ, бот не админ канала,
        неожиданная форма ответа) → None. Вызывающий код выбирает fail-open либо
        fail-closed политику для конкретного действия.
        """
        try:
            response = await self._client.post(
                f"{self._base}getChatMember",
                json={"chat_id": f"@{self._channel_username}", "user_id": user_id},
            )
        except (httpx.HTTPError, OSError) as exc:
            _LOGGER.warning(
                "trial_channel_gate.get_chat_member_failed user_id=%s error=%r",
                user_id,
                exc,
            )
            return None
        try:
            data = response.json()
        except ValueError:
            _LOGGER.warning(
                "trial_channel_gate.get_chat_member_bad_json status=%s user_id=%s",
                response.status_code,
                user_id,
            )
            return None
        if not isinstance(data, dict) or data.get("ok") is not True:
            _LOGGER.warning(
                "trial_channel_gate.get_chat_member_not_ok status=%s user_id=%s",
                response.status_code,
                user_id,
            )
            return None
        member = data.get("result")
        if not isinstance(member, dict):
            _LOGGER.warning(
                "trial_channel_gate.get_chat_member_bad_result user_id=%s",
                user_id,
            )
            return None
        status = member.get("status")
        if status in ("creator", "administrator", "member"):
            return True
        if status == "restricted":
            # restricted с is_member=True остаётся подписчиком (мьют и т.п. не в счёт).
            return bool(member.get("is_member"))
        return False
