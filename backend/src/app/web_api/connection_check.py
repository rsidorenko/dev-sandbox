"""Connection Check — публичные (bearer-токен /sub) эндпоинты проверки подключения.

Страница /check/<token> определяет по IP запроса, идёт ли трафик через наши VPN-серверы,
замеряет задержку/скорость и присылает результат; кнопки вердикта пишут в существующие
funnel-события. IP-адреса не хранятся и не логируются; все записи fail-open.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from app.application.connection_check import (
    connection_check_enabled,
    match_server_for_ip,
    real_client_ip,
    record_connection_check,
    resolve_active_server_ips,
)
from app.application.funnel_events import record_interaction
from app.application.trial_feedback import CONNECTION_PROBLEM_REASONS, record_trial_feedback
from app.web_api.helpers import safe_json_error
from app.web_api.subscription import _entitlement_active

_LOGGER = logging.getLogger(__name__)

_SPEED_MIN_BYTES = 65536
_SPEED_MAX_BYTES = 1048576
_SPEED_DEFAULT_BYTES = 262144

# In-memory rate limit по IP (как у /sub): bucket → окно запросов.
_RATE_BUCKETS: dict[str, tuple[int, int]] = {
    "status": (30, 60),
    "ping": (90, 60),
    "speed": (10, 60),
    "write": (30, 60),
}
_rate_limit_store: dict[str, list[float]] = {}
_rate_last_cleanup = 0.0
_RATE_MAX_ENTRIES = 20000


def _check_rate_limit(bucket: str, client_ip: str) -> bool:
    global _rate_last_cleanup
    now = time.monotonic()
    limit, window = _RATE_BUCKETS.get(bucket, (10, 60))
    if now - _rate_last_cleanup > window:
        expired = [k for k, v in _rate_limit_store.items() if not v or now - v[-1] > window]
        for k in expired:
            del _rate_limit_store[k]
        if len(_rate_limit_store) > _RATE_MAX_ENTRIES:
            oldest = sorted(_rate_limit_store, key=lambda k: _rate_limit_store[k][-1] if _rate_limit_store[k] else 0)
            for k in oldest[: len(_rate_limit_store) - _RATE_MAX_ENTRIES]:
                del _rate_limit_store[k]
        _rate_last_cleanup = now
    key = f"{bucket}:{client_ip}"
    hits = _rate_limit_store.get(key, [])
    hits = [t for t in hits if now - t < window]
    if len(hits) >= limit:
        _rate_limit_store[key] = hits
        return False
    hits.append(now)
    _rate_limit_store[key] = hits
    return True


async def _load_token_row(pool, token: str):
    """user_identities ⋈ subscription_snapshots по subscription_token (как /sub)."""
    return await pool.fetchrow(
        """SELECT i.internal_user_id,
                  s.state_label, s.trial_expires_at, s.active_until_utc, s.keys_deleted_at
           FROM user_identities i
           LEFT JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id
           WHERE i.subscription_token = $1""",
        token,
    )


def _detect(request: Request, servers_by_label: dict) -> tuple[bool | None, str, str]:
    """(connected, server_label, country_code). connected=None — не удалось определить
    (серверы не резолвятся): честный «unknown» вместо ложного «не подключён»."""
    client_ip = real_client_ip(request)
    if not servers_by_label:
        return None, "", ""
    matched = match_server_for_ip(client_ip, servers_by_label)
    if matched is None:
        return False, "", ""
    info = servers_by_label.get(matched, {})
    return True, matched, str(info.get("country_code", ""))


async def handle_check_status(request: Request) -> JSONResponse:
    if not connection_check_enabled():
        return JSONResponse({"ok": True, "enabled": False})
    client_ip = real_client_ip(request)
    if not _check_rate_limit("status", client_ip):
        return safe_json_error(429, "rate_limited")

    token = request.path_params["token"]
    pool = request.app.state.pool
    row = await _load_token_row(pool, token)
    if row is None:
        return safe_json_error(404, "not_found")
    if not _entitlement_active(row, now=datetime.now(UTC)):
        return safe_json_error(403, "subscription_expired")

    servers_by_label = await resolve_active_server_ips(pool)
    connected, server_label, country_code = _detect(request, servers_by_label)
    return JSONResponse(
        {
            "ok": True,
            "enabled": True,
            "connected": connected,
            "server_label": server_label,
            "server_country": country_code,
        }
    )


async def handle_check_ping(request: Request) -> PlainTextResponse:
    if not connection_check_enabled():
        return PlainTextResponse("disabled", status_code=404)
    client_ip = real_client_ip(request)
    if not _check_rate_limit("ping", client_ip):
        return PlainTextResponse("rate limited", status_code=429)
    return PlainTextResponse(
        "1",
        media_type="text/plain",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


async def handle_check_speed(request: Request) -> Response:
    if not connection_check_enabled():
        return Response("disabled", status_code=404)
    client_ip = real_client_ip(request)
    if not _check_rate_limit("speed", client_ip):
        return Response("rate limited", status_code=429)
    try:
        requested = int(request.query_params.get("bytes", ""))
    except ValueError:
        requested = _SPEED_DEFAULT_BYTES
    size = max(_SPEED_MIN_BYTES, min(_SPEED_MAX_BYTES, requested))
    payload = os.urandom(size)
    return Response(
        payload,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


def _validated_platform(payload: dict) -> str:
    platform = payload.get("platform")
    return platform if platform in ("ios", "android", "mac", "win", "tv") else ""


def _metric_int(payload: dict, key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


async def handle_check_result(request: Request) -> JSONResponse:
    if not connection_check_enabled():
        return safe_json_error(404, "disabled")
    client_ip = real_client_ip(request)
    if not _check_rate_limit("write", client_ip):
        return safe_json_error(429, "rate_limited")

    token = request.path_params["token"]
    pool = request.app.state.pool
    row = await _load_token_row(pool, token)
    if row is None:
        return safe_json_error(404, "not_found")
    if not _entitlement_active(row, now=datetime.now(UTC)):
        return safe_json_error(403, "subscription_expired")

    try:
        payload = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_json")
    if not isinstance(payload, dict):
        return safe_json_error(400, "invalid_payload")

    # connected/server_label определяет сервер по IP ЭТОГО запроса — клиентскому
    # self-report не доверяем (подделывается).
    servers_by_label = await resolve_active_server_ips(pool)
    connected, server_label, _ = _detect(request, servers_by_label)
    stored = await record_connection_check(
        pool,
        row["internal_user_id"],
        connected=connected is True,
        server_label=server_label,
        latency_ms=_metric_int(payload, "latency_ms"),
        throughput_kbps=_metric_int(payload, "throughput_kbps"),
        platform=_validated_platform(payload),
    )
    return JSONResponse({"ok": True, "stored": stored})


async def handle_check_feedback(request: Request) -> JSONResponse:
    if not connection_check_enabled():
        return safe_json_error(404, "disabled")
    client_ip = real_client_ip(request)
    if not _check_rate_limit("write", client_ip):
        return safe_json_error(429, "rate_limited")

    token = request.path_params["token"]
    pool = request.app.state.pool
    row = await _load_token_row(pool, token)
    if row is None:
        return safe_json_error(404, "not_found")
    if not _entitlement_active(row, now=datetime.now(UTC)):
        return safe_json_error(403, "subscription_expired")

    try:
        payload = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_json")
    if not isinstance(payload, dict):
        return safe_json_error(400, "invalid_payload")

    verdict = payload.get("verdict")
    if verdict not in ("ok", "problem"):
        return safe_json_error(400, "invalid_verdict")
    platform = _validated_platform(payload)
    reason_code = payload.get("reason_code")
    if reason_code is not None and reason_code not in CONNECTION_PROBLEM_REASONS:
        return safe_json_error(400, "invalid_reason")
    if verdict == "ok" and reason_code is not None:
        return safe_json_error(400, "invalid_reason")

    internal_user_id = row["internal_user_id"]
    event_name = "connection_confirmed" if verdict == "ok" else "connection_problem"
    day = datetime.now(UTC).strftime("%Y%m%d")
    await record_interaction(
        pool,
        event_name=event_name,
        surface="web",
        dedup_key=f"webcheck:{internal_user_id}:{day}",
        internal_user_id=internal_user_id,
        platform=platform,
    )
    feedback_stored = False
    if verdict == "problem" and reason_code is not None:
        feedback_stored = await record_trial_feedback(
            pool,
            feedback_type="connection_problem",
            reason_code=reason_code,
            internal_user_id=internal_user_id,
            platform=platform,
            connection_route="",
        )
    return JSONResponse({"ok": True, "feedback_stored": feedback_stored})
