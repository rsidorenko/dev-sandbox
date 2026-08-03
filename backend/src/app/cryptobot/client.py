"""CryptoBot Crypto Pay API client — create fiat-RUB invoices and re-fetch their status.

Crypto is a *payment method*, RUB remains the denomination: every invoice is created
with ``currency_type="fiat", fiat="RUB", amount=<plan price RUB>``. The user pays the
crypto equivalent shown by CryptoBot; the invoice's RUB face value is fixed by us, so
``amount_kopecks`` stays RUB throughout the fulfillment pipeline (identical to YooKassa
→ referral commissions, promo and balances compute the same).

Auth: the app API token in the ``Crypto-Pay-API-Token`` header. Base URL is
``https://pay.crypt.bot/api`` (prod) or the testnet host when ``CRYPTOBOT_TESTNET``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

_LOGGER = logging.getLogger(__name__)

_PROD_API_BASE = "https://pay.crypt.bot/api"
_TESTNET_API_BASE = "https://testnet-pay.crypt.bot/api"

ENV_CRYPTOBOT_API_TOKEN = "CRYPTOBOT_API_TOKEN"
ENV_CRYPTOBOT_TESTNET = "CRYPTOBOT_TESTNET"


@dataclass(frozen=True, slots=True)
class CryptoBotInvoiceResult:
    invoice_id: str
    pay_url: str


@dataclass(frozen=True, slots=True)
class CryptoBotInvoiceInfo:
    invoice_id: str
    status: str
    amount: str  # the asset/crypto amount actually paid
    fiat: str  # the fiat currency code ("RUB")
    fiat_amount: str  # the fiat amount we set at creation (== expected RUB)
    payload: str  # our round-tripped payment context


class CryptoBotError(RuntimeError):
    """Raised on a non-OK Crypto Pay API response."""


class CryptoBotClient:
    """Thin wrapper over the Crypto Pay API (createInvoice / getInvoices)."""

    def __init__(
        self,
        *,
        api_token: str,
        http_client: httpx.AsyncClient | None = None,
        testnet: bool = False,
    ) -> None:
        self._api_token = api_token
        self._testnet = testnet
        self._client = http_client or httpx.AsyncClient(timeout=30.0)
        self._base = _TESTNET_API_BASE if testnet else _PROD_API_BASE

    @classmethod
    def from_env(cls) -> CryptoBotClient | None:
        api_token = os.environ.get(ENV_CRYPTOBOT_API_TOKEN, "").strip()
        if not api_token:
            return None
        testnet = os.environ.get(ENV_CRYPTOBOT_TESTNET, "").strip().lower() in ("1", "true", "yes")
        return cls(api_token=api_token, testnet=testnet)

    def _headers(self) -> dict[str, str]:
        return {"Crypto-Pay-API-Token": self._api_token, "Content-Type": "application/json"}

    async def _post(self, method: str, body: dict) -> dict:
        resp = await self._client.post(f"{self._base}/{method}", json=body, headers=self._headers())
        if resp.status_code >= 400:
            _LOGGER.error("cryptobot %s failed status=%d body=%s", method, resp.status_code, resp.text[:500])
            raise CryptoBotError(f"CryptoBot API error: {resp.status_code}")
        data = resp.json()
        if not data.get("ok"):
            _LOGGER.error("cryptobot %s non-ok response=%s", method, str(data)[:500])
            raise CryptoBotError(f"CryptoBot API non-ok: {data.get('error', 'unknown')}")
        return data.get("result") or {}

    async def create_invoice(
        self,
        *,
        amount_rubles: int,
        payload_str: str,
        description: str,
        paid_btn_url: str,
        expires_in: int,
    ) -> CryptoBotInvoiceResult:
        """Create a fiat-RUB invoice. The user pays the crypto equivalent; the RUB face
        value is fixed by us (anti-tamper anchor for the webhook). Returns the invoice
        id and a user-facing payment URL.
        """
        body = {
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": str(amount_rubles),
            "description": description,
            "payload": payload_str,
            "paid_btn_name": "openBot",
            "paid_btn_url": paid_btn_url,
            "allow_anonymous": False,
            "expires_in": expires_in,
        }
        result = await self._post("createInvoice", body)
        invoice_id = str(result.get("invoice_id", ""))
        # CryptoBot exposes several URL fields across API versions — prefer the
        # mini-app/web URL, fall back to the bot invoice URL, then the legacy pay link.
        pay_url = result.get("mini_app_url") or result.get("bot_invoice_url") or result.get("pay_link") or ""
        if not invoice_id or not pay_url:
            raise CryptoBotError(f"CryptoBot createInvoice missing fields: {str(result)[:300]}")
        _LOGGER.info("cryptobot invoice created id=%s amount_rub=%d", invoice_id, amount_rubles)
        return CryptoBotInvoiceResult(invoice_id=invoice_id, pay_url=pay_url)

    async def get_invoice(self, invoice_id: str) -> CryptoBotInvoiceInfo | None:
        """Re-fetch a single invoice (object-status auth — the authoritative source for
        ``status`` and ``payload``). Returns None when the invoice cannot be retrieved.
        """
        try:
            result = await self._post("getInvoices", {"invoice_ids": [str(invoice_id)]})
        except CryptoBotError as exc:
            _LOGGER.warning("cryptobot getInvoices failed id=%s: %s", invoice_id, exc)
            return None
        items = result.get("items") or []
        if not items:
            return None
        inv = items[0]
        return _invoice_to_info(inv)


def _invoice_to_info(inv: dict) -> CryptoBotInvoiceInfo:
    return CryptoBotInvoiceInfo(
        invoice_id=str(inv.get("invoice_id", "")),
        status=str(inv.get("status", "")),
        amount=str(inv.get("amount", "0")),
        fiat=str(inv.get("fiat", "")),
        fiat_amount=str(inv.get("fiat_amount", inv.get("amount", "0"))),
        payload=str(inv.get("payload", "")),
    )
