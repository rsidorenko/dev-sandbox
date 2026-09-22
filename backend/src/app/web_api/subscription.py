"""Public subscription endpoint: /sub/{token} → base64-encoded VLESS links.
With SUB_HAPP_ROUTING_ENABLE the response also carries the `routing` header —
a Happ routing profile that routes RU app families (Ozon/MAX/VK/Яндекс.Музыка/
Т-Банк) DIRECT (see subscription_happ_routing.py). With
SUB_PROFILE_META_ENABLE it carries the standard subscription metadata headers
(profile-title / subscription-userinfo / support-url / profile-update-interval
/ announce with remaining days — see _profile_meta_headers)."""

from __future__ import annotations

import base64
import math
import os
import time
from datetime import UTC, datetime
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, Response

from app.domain.access_entitlement import entitlement_active as _entitlement_active
from app.issuance.vless_provider import VlessProviderOutcome, VlessServerConfig
from app.persistence.postgres_traffic_stats import PostgresTrafficStatsRepository
from app.web_api.subscription_happ_routing import build_routing_header_value

_SUB_RATE_LIMIT_MAX = 30
_SUB_RATE_LIMIT_WINDOW = 60
_SUB_RATE_LIMIT_MAX_ENTRIES = 10000
_sub_rate_limit_store: dict[str, list[float]] = {}
_sub_last_cleanup: float = 0.0

# Attach a Happ routing profile (RU app families — Ozon/MAX/VK/Яндекс.Музыка/
# Т-Банк → DIRECT from the subscriber's own ISP IP) to the base64 response as
# the `routing` header. Happ-only by UA gate; every other client ignores
# unknown response headers. Off by default: opt-in per env, see
# subscription_happ_routing.py.
_HAPP_ROUTING_ENABLE = os.environ.get("SUB_HAPP_ROUTING_ENABLE", "").strip().lower() in ("1", "true", "yes")

# Standard subscription metadata headers (Happ docs → App management, standard
# parameters — no Provider ID needed): profile title with emoji, remaining days
# via subscription-userinfo expire + an «Осталось: N дней» announce, a Telegram
# "airplane" icon linking to our bot via support-url, and a 12 h auto-update so
# the days stay fresh. Off by default: opt-in per env, mirrors
# SUB_HAPP_ROUTING_ENABLE.
_PROFILE_META_ENABLE = os.environ.get("SUB_PROFILE_META_ENABLE", "").strip().lower() in ("1", "true", "yes")
_DEFAULT_PROFILE_TITLE = "🛡️ Bravada VPN"


def _check_sub_rate_limit(client_ip: str) -> bool:
    global _sub_last_cleanup
    now = time.monotonic()
    if now - _sub_last_cleanup > _SUB_RATE_LIMIT_WINDOW:
        expired = [k for k, v in _sub_rate_limit_store.items() if not v or now - v[-1] > _SUB_RATE_LIMIT_WINDOW]
        for k in expired:
            del _sub_rate_limit_store[k]
        if len(_sub_rate_limit_store) > _SUB_RATE_LIMIT_MAX_ENTRIES:
            oldest = sorted(
                _sub_rate_limit_store, key=lambda k: _sub_rate_limit_store[k][-1] if _sub_rate_limit_store[k] else 0
            )
            for k in oldest[: len(_sub_rate_limit_store) - _SUB_RATE_LIMIT_MAX_ENTRIES]:
                del _sub_rate_limit_store[k]
        _sub_last_cleanup = now
    window = _sub_rate_limit_store.get(client_ip, [])
    window = [t for t in window if now - t < _SUB_RATE_LIMIT_WINDOW]
    if len(window) >= _SUB_RATE_LIMIT_MAX:
        _sub_rate_limit_store[client_ip] = window
        return False
    window.append(now)
    _sub_rate_limit_store[client_ip] = window
    return True


async def handle_subscription(request: Request) -> PlainTextResponse | Response:
    client_ip = request.client.host if request.client else "unknown"
    if not _check_sub_rate_limit(client_ip):
        return Response("rate limited", status_code=429)

    pool = request.app.state.pool
    token = request.path_params["token"]

    # ?open=karing|v2raytun → redirect to app URL scheme for auto-import
    open_action = request.query_params.get("open")
    if open_action in ("karing", "v2raytun"):
        # Build https URL — behind nginx the scheme is http
        forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        sub_url = f"{forwarded_proto}://{request.url.netloc}{request.url.path}"
        if open_action == "karing":
            app_url = f"karing://install-config?url={quote(sub_url, safe='')}&name=Bravada%20VPN"
        else:
            app_url = f"v2raytun://import/{sub_url}"
        return HTMLResponse(
            f'<html><head><meta http-equiv="refresh" content="0;url={app_url}">'
            f"</head><body><p>Opening {open_action}...</p>"
            f'<p><a href="{app_url}">Tap here if nothing happens</a></p></body></html>'
        )

    row = await pool.fetchrow(
        """SELECT i.internal_user_id,
                  s.state_label, s.plan_id, s.trial_expires_at, s.active_until_utc, s.keys_deleted_at
           FROM user_identities i
           LEFT JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id
           WHERE i.subscription_token = $1""",
        token,
    )
    if row is None:
        return PlainTextResponse("not found", status_code=404)
    # Entitlement gate: serve keys only while the subscription/trial is currently valid.
    # bare_xray links are built deterministically (no panel probe), so without this gate
    # /sub/ keeps handing keys to expired users even after reconcile removed them from
    # the inbounds — the keys are dead but the app still imports them. Mirrors the
    # reconcile_bare_xray desired-set so /sub/ and reconcile agree on who gets keys.
    if not _entitlement_active(row, now=datetime.now(UTC)):
        return PlainTextResponse("subscription expired", status_code=403)

    provider = request.app.state.vless_provider

    result = await provider.get_user_config(internal_user_id=row["internal_user_id"])
    if result.outcome != VlessProviderOutcome.SUCCESS or result.config is None:
        return PlainTextResponse("unavailable", status_code=503)

    ua = request.headers.get("user-agent", "")
    client_param = request.query_params.get("client")
    servers = _select_servers_for_client(result.config.servers, ua=ua, client_param=client_param)

    links = "\n".join(s.vless_link for s in servers)
    encoded = base64.b64encode(links.encode("utf-8")).decode("utf-8")
    response = PlainTextResponse(encoded, media_type="text/plain")
    if _PROFILE_META_ENABLE:
        # Standard headers read by Happ/Karing/v2rayN; unknown ones are ignored
        # by other clients. Title goes base64 (UTF-8 emoji is not header-safe).
        upload_bytes, download_bytes = await PostgresTrafficStatsRepository(pool).monthly_usage_for_user(
            row["internal_user_id"]
        )
        for name, value in _profile_meta_headers(row, upload_bytes=upload_bytes, download_bytes=download_bytes).items():
            response.headers[name] = value
    if _HAPP_ROUTING_ENABLE and _wants_happ(ua, client_param):
        # Happ adds + activates the profile from this header on import/update;
        # the body stays the plain link list, so the server picker is untouched.
        response.headers["routing"] = build_routing_header_value()
    return response


def _profile_meta_headers(
    row,
    *,
    upload_bytes: int = 0,
    download_bytes: int = 0,
    now: datetime | None = None,
) -> dict[str, str]:
    """Standard subscription metadata headers for /sub/ (Happ docs → App
    management, standard parameters — no Provider ID required).

    * ``profile-title`` — «🛡️ Bravada VPN» (env ``SUB_PROFILE_TITLE``
      overrides), emitted as ``base64:<b64>`` because the emoji is not
      ASCII/header-safe; Happ limit is 25 decoded chars (ours is 14).
    * ``profile-update-interval`` — 12 h auto-update, so remaining days and
      renewals propagate without a manual refresh.
    * ``subscription-userinfo`` — ``expire`` = the later of trial/paid windows
      (same "later wins" as _entitlement_active); Happ renders remaining days
      from it and flips the profile to expired once it passes. ``upload`` and
      ``download`` contain actual traffic from the current MSK month; ``total``
      stays zero because we sell time, not bytes, so clients show usage / ∞.
    * ``announce`` — «⏳ Осталось: N дней» with a live day count (ceil of the
      time left to the same expiry). Happ draws its own «Истекает: <дата>»
      from ``expire``; the native «N days» status chip only renders when the
      userinfo carries traffic quota, which we deliberately do not sell — so
      the announcement is the standard no-Provider-ID way to show days.
      Base64-encoded (Cyrillic + emoji are not header-safe).
    * ``support-url`` — ``https://t.me/<BOT_USERNAME>``: Happ draws the
      Telegram airplane icon on the subscription bar and opens our bot.

    Headers whose inputs are missing (no expiry timestamps, no
    ``BOT_USERNAME``) are simply not emitted; ``announce`` is also dropped
    once the expiry has passed (the expired state speaks for itself).
    """
    headers: dict[str, str] = {}
    title = os.environ.get("SUB_PROFILE_TITLE", "").strip() or _DEFAULT_PROFILE_TITLE
    headers["profile-title"] = "base64:" + base64.b64encode(title.encode("utf-8")).decode("ascii")
    headers["profile-update-interval"] = "12"
    expires = [d for d in (row.get("trial_expires_at"), row.get("active_until_utc")) if d is not None]
    if expires:
        expire_at = max(expires)
        headers["subscription-userinfo"] = (
            f"upload={max(upload_bytes, 0)}; download={max(download_bytes, 0)}; "
            f"total=0; expire={int(expire_at.timestamp())}"
        )
        remaining = expire_at - (now or datetime.now(UTC))
        if remaining.total_seconds() > 0:
            days = math.ceil(remaining.total_seconds() / 86400)
            announce = f"⏳ Осталось: {_ru_days(days)}"
            headers["announce"] = "base64:" + base64.b64encode(announce.encode("utf-8")).decode("ascii")
    bot_username = os.environ.get("BOT_USERNAME", "").strip().lstrip("@")
    if bot_username:
        headers["support-url"] = f"https://t.me/{bot_username}"
    return headers


def _ru_days(count: int) -> str:
    """«N день/дня/дней» — Russian plural for a day count (1 день, 2 дня, 5 дней,
    11 дней, 21 день…)."""
    if count % 10 == 1 and count % 100 != 11:
        form = "день"
    elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        form = "дня"
    else:
        form = "дней"
    return f"{count} {form}"


def _wants_happ(ua: str, client_param: str | None) -> bool:
    """Whether the importing client is Happ (UA substring, or ?client=happ override)."""
    return "happ" in (ua or "").lower() or client_param == "happ"


def _select_servers_for_client(
    servers: tuple[VlessServerConfig, ...] | list[VlessServerConfig],
    *,
    ua: str,
    client_param: str | None,
) -> list[VlessServerConfig]:
    """Pick which server links to serve based on the importing client.

    A Happ import (User-Agent contains 'happ', or ?client=happ override) gets only
    surface='happ' entries — the bare_xray RU-relay entry (Cloud.ru) that works in
    Happ; the foreign 3x-ui fleet does not. Every other client (Karing, v2rayN, …)
    gets surface='default' (the fleet), exactly as before. Falls back to the full set
    when the requested surface is empty (e.g. Cloud.ru row inactive).

    surface='both' is served to EVERY client (Happ and default) — used for entries
    that should appear in both subscriptions, e.g. a Cloud.ru 1.0 relay promoted from
    Happ-only to universal once the direct fleet 1.0 is DPI-dead for Karing.
    """
    want_happ = _wants_happ(ua, client_param)
    _FC = {0x59: 1, 0x0413: 2, 0x0424: 3, 0x041B: 4, 0x0421: 5, 0x041E: 6}

    def _sort_key(server: VlessServerConfig) -> tuple[int, int]:
        # Виртуальные точки выбора должны быть видны до ручного списка стран.
        auto_rank = 0 if "автовыбор" in server.server_label.lower() else 1
        first = ord(server.server_label[0]) if server.server_label else 0
        return auto_rank, _FC.get(first, 99)

    servers = sorted(servers, key=_sort_key)
    both = [s for s in servers if s.surface == "both"]
    if want_happ:
        happ = [s for s in servers if s.surface == "happ"]
        selected = both + happ
        return selected if selected else list(servers)
    return both + [s for s in servers if s.surface == "default"]
