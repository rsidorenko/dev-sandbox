"""Выдача временной ссылки на экране; адрес электронной почты не принимается."""

from __future__ import annotations

import re
import time

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.persistence.pending_web_links import create_link
from app.shared.site_url import get_site_base_url
from app.web_api.helpers import safe_json_error

_MAX_REQUESTS_PER_IP_PER_HOUR = 5
_RATE_LIMIT_WINDOW = 3600
_RATE_LIMIT_MAX_ENTRIES = 10000
_rate_limit_store: dict[str, list[float]] = {}
_rate_limit_last_cleanup = 0.0

# Формат реферальной метки.
_REF_CODE_RE = re.compile(r"^[a-z0-9]{4,32}$", re.IGNORECASE)


def _check_rate_limit(client_ip: str) -> bool:
    """Ограничиваем создание временных ссылок с одного адреса."""
    global _rate_limit_last_cleanup
    now = time.monotonic()
    if now - _rate_limit_last_cleanup > _RATE_LIMIT_WINDOW:
        expired = [k for k, v in _rate_limit_store.items() if not v or now - v[-1] > _RATE_LIMIT_WINDOW]
        for k in expired:
            del _rate_limit_store[k]
        if len(_rate_limit_store) > _RATE_LIMIT_MAX_ENTRIES:
            oldest = sorted(_rate_limit_store, key=lambda k: _rate_limit_store[k][-1] if _rate_limit_store[k] else 0)
            for k in oldest[: len(_rate_limit_store) - _RATE_LIMIT_MAX_ENTRIES]:
                del _rate_limit_store[k]
        _rate_limit_last_cleanup = now
    window = _rate_limit_store.get(client_ip, [])
    window = [t for t in window if now - t < _RATE_LIMIT_WINDOW]
    if len(window) >= _MAX_REQUESTS_PER_IP_PER_HOUR:
        _rate_limit_store[client_ip] = window
        return False
    window.append(now)
    _rate_limit_store[client_ip] = window
    return True


async def handle_link_request(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request")
    if not isinstance(data, dict) or any(k not in ("referral_code",) for k in data):
        return safe_json_error(400, "invalid_request")
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return safe_json_error(429, "rate_limited")
    referral_code = str(data.get("referral_code", "")).strip()
    if not _REF_CODE_RE.fullmatch(referral_code):
        referral_code = None
    row = await create_link(request.app.state.pool, referral_code)
    return JSONResponse(
        {
            "ok": True,
            "subscription_url": f"{get_site_base_url()}/sub/{row['subscription_token']}",
            "expires_at": row["expires_at"].isoformat(),
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
