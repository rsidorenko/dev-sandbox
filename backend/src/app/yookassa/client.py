"""YooKassa API v3 client — create payments and verify payment status."""

from __future__ import annotations

import base64
import logging
import os
import uuid
from dataclasses import dataclass

import httpx

_LOGGER = logging.getLogger(__name__)

_YOOKASSA_API_BASE = "https://api.yookassa.ru/v3"

ENV_YOOKASSA_SHOP_ID = "YOOKASSA_SHOP_ID"
ENV_YOOKASSA_API_KEY = "YOOKASSA_API_KEY"


@dataclass(frozen=True, slots=True)
class YooKassaPaymentResult:
    payment_id: str
    confirmation_url: str


@dataclass(frozen=True, slots=True)
class YooKassaPaymentInfo:
    payment_id: str
    status: str
    amount_value: str
    metadata: dict[str, str]


class YooKassaClient:
    """Thin wrapper over YooKassa Payments API."""

    def __init__(
        self,
        *,
        shop_id: str,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._shop_id = shop_id
        self._api_key = api_key
        self._client = http_client or httpx.AsyncClient(timeout=30.0)

    @classmethod
    def from_env(cls) -> YooKassaClient | None:
        shop_id = os.environ.get(ENV_YOOKASSA_SHOP_ID, "").strip()
        api_key = os.environ.get(ENV_YOOKASSA_API_KEY, "").strip()
        if not shop_id or not api_key:
            return None
        return cls(shop_id=shop_id, api_key=api_key)

    def _auth_header(self) -> str:
        return base64.b64encode(
            f"{self._shop_id}:{self._api_key}".encode()
        ).decode("ascii")

    async def create_payment(
        self,
        *,
        amount_rubles: int,
        plan_id: str,
        device_count: int,
        telegram_user_id: int,
        return_url: str,
        description: str = "Bravada VPN subscription",
        metadata: dict[str, str] | None = None,
    ) -> YooKassaPaymentResult:
        idempotency_key = str(uuid.uuid4())

        body: dict = {
            "amount": {
                "value": f"{amount_rubles}.00",
                "currency": "RUB",
            },
            "confirmation": {
                "type": "redirect",
                "return_url": return_url,
            },
            "capture": True,
            "description": description,
            "metadata": {
                "plan_id": plan_id,
                "device_count": str(device_count),
                "telegram_user_id": str(telegram_user_id),
                **(metadata or {}),
            },
        }

        resp = await self._client.post(
            f"{_YOOKASSA_API_BASE}/payments",
            json=body,
            headers={
                "Authorization": f"Basic {self._auth_header()}",
                "Idempotence-Key": idempotency_key,
                "Content-Type": "application/json",
            },
        )

        if resp.status_code >= 400:
            _LOGGER.error(
                "yookassa create_payment failed status=%d body=%s",
                resp.status_code,
                resp.text[:500],
            )
            raise RuntimeError(f"YooKassa API error: {resp.status_code}")

        data = resp.json()
        payment_id = data["id"]
        confirmation_url = data["confirmation"]["confirmation_url"]

        _LOGGER.info(
            "yookassa payment created id=%s amount=%d",
            payment_id,
            amount_rubles,
        )
        return YooKassaPaymentResult(
            payment_id=payment_id,
            confirmation_url=confirmation_url,
        )

    async def get_payment(self, payment_id: str) -> YooKassaPaymentInfo | None:
        """Fetch payment details from YooKassa API to verify webhook authenticity."""
        try:
            resp = await self._client.get(
                f"{_YOOKASSA_API_BASE}/payments/{payment_id}",
                headers={
                    "Authorization": f"Basic {self._auth_header()}",
                },
            )
        except (httpx.HTTPError, OSError) as exc:
            _LOGGER.warning("yookassa get_payment request failed: %s", exc)
            return None

        if resp.status_code == 404:
            _LOGGER.warning("yookassa get_payment not found id=%s", payment_id)
            return None

        if resp.status_code >= 400:
            _LOGGER.error(
                "yookassa get_payment failed status=%d id=%s",
                resp.status_code,
                payment_id,
            )
            return None

        data = resp.json()
        metadata = {}
        raw_meta = data.get("metadata")
        if isinstance(raw_meta, dict):
            metadata = {str(k): str(v) for k, v in raw_meta.items()}

        amount_obj = data.get("amount", {})
        amount_value = amount_obj.get("value", "0") if isinstance(amount_obj, dict) else "0"

        return YooKassaPaymentInfo(
            payment_id=data.get("id", payment_id),
            status=data.get("status", ""),
            amount_value=str(amount_value),
            metadata=metadata,
        )
