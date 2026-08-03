"""CryptoBot invoice ``payload`` codec — carry the payment context through the invoice.

CryptoBot's ``createInvoice`` accepts a single arbitrary ``payload`` string that is
echoed back verbatim in the webhook/update and in ``getInvoices``. We use it the
same way YooKassa uses its ``metadata`` dict: to round-trip the payment context
(``plan_id, device_count, telegram_user_id, internal_user_id, promo, kind, ...``)
from creation to the webhook, so the shared fulfillment helpers receive the same
``metadata: dict[str, str]`` shape regardless of provider.

Encoding is base64url(JSON) — robust against separators/unicode and well within
CryptoBot's payload length budget.
"""

from __future__ import annotations

import base64
import json


def encode_payment_context(context: dict[str, object]) -> str:
    """Serialize a payment-context dict into a CryptoBot ``payload`` string."""
    raw = json.dumps(context, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_payment_context(payload: str | None) -> dict[str, str]:
    """Inverse of :func:`encode_payment_context`.

    Returns an empty dict when *payload* is missing/blank or cannot be decoded, so
    callers fall through to their existing invalid-payload paths unchanged. All
    values are coerced to ``str`` to match the ``metadata: dict[str, str]`` contract
    the shared webhook helpers expect (mirrors YooKassa metadata typing).
    """
    if not payload or not isinstance(payload, str):
        return {}
    try:
        pad = "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode((payload + pad).encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}
