"""Public adapter wrapping a raw Bot API client into the
``FulfillmentActivationTelegramNotifier`` protocol.

Used by the YooKassa-webhook fulfillment path (``telegram_webhook_main``),
the web balance-payment handler, and (Phase 3) the bot balance-payment path —
so proactive activation / commission notices go through one place.
"""

from __future__ import annotations

from typing import Any

from app.runtime.telegram_httpx_raw_client import HttpxTelegramRawPollingClient


class FulfillmentActivationTelegramNotifierAdapter:
    """Delegates proactive activation / commission copy to the shared raw Bot API client."""

    __slots__ = ("_client",)

    def __init__(self, client: HttpxTelegramRawPollingClient) -> None:
        self._client = client

    async def send_subscription_activated_notice(
        self,
        *,
        telegram_user_id: int,
        text: str,
        reply_markup: dict[str, Any] | None,
        correlation_id: str,
    ) -> None:
        await self._client.send_text_message(
            chat_id=telegram_user_id,
            text=text,
            correlation_id=correlation_id,
            reply_markup=reply_markup,
        )


# Backwards-compat alias: telegram_webhook_main historically used the private name.
_FulfillmentActivationTelegramNotifier = FulfillmentActivationTelegramNotifierAdapter
