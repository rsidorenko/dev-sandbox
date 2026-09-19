"""Серверная часть Connection Check: IP-детект «трафик идёт через наш VPN» и запись результата.

Privacy: IP-адреса пользователей не сохраняются и не логируются — только факт совпадения
с адресом одного из наших серверов, метка сервера и метрики качества (fail-open, как funnel_events).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

# TTL кеша DNS-резолва server_host → IP. Резолв внешних имён в запросе дорого и
# может мигать; 10 минут держат баланс между свежестью адресов и задержкой.
_DNS_CACHE_TTL_SECONDS = 600.0
_dns_cache: dict[str, tuple[float, frozenset[str]]] = {}

_LATENCY_MIN_MS = 0
_LATENCY_MAX_MS = 60000
_THROUGHPUT_MIN_KBPS = 0
_THROUGHPUT_MAX_KBPS = 1000000


def connection_check_enabled() -> bool:
    return os.environ.get("CONNECTION_CHECK_ENABLE", "").strip().lower() in ("1", "true", "yes", "on")


def real_client_ip(request: Any) -> str:
    """Клиентский IP за nginx: X-Real-IP (nginx перезаписывает его $remote_addr и
    клиент подделать не может), fallback — TCP-пир uvicorn.

    Невалидное значение заголовка трактуем как отсутствие (защита от мусора/спуфа
    при прямом доступе к порту без nginx — порт backend не публикуется, но
    защищаемся и от внутренней ошибки).
    """
    header_ip = (request.headers.get("x-real-ip") or "").strip()
    if header_ip:
        try:
            ipaddress.ip_address(header_ip)
            return header_ip
        except ValueError:
            pass
    return request.client.host if request.client else ""


async def _resolve_host_ips(host: str) -> frozenset[str]:
    """Резолв hostname → множество IP-строк (v4+v6). Ошибка/пусто → пустое множество
    (fail-open: проверка ответит «не удалось определить», а не упадёт)."""
    now = time.monotonic()
    cached = _dns_cache.get(host)
    if cached is not None and now - cached[0] < _DNS_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        loop = asyncio.get_running_loop()
        resolved = await asyncio.wait_for(
            loop.run_in_executor(None, _blocking_getaddrinfo, host),
            timeout=1.5,
        )
        ips = frozenset(resolved)
    except Exception:
        # Служебный сбой резолва: отдаём протухший кеш, если есть, иначе пусто.
        return cached[1] if cached is not None else frozenset()
    _dns_cache[host] = (now, ips)
    return ips


def _blocking_getaddrinfo(host: str) -> set[str]:
    import socket

    return {info[4][0] for info in socket.getaddrinfo(host, None)}


async def resolve_active_server_ips(pool: Any) -> dict[str, dict[str, Any]]:
    """Активные vpn_servers: label → {"ips": frozenset, "country_code": str}
    (все client_surface: матчимся по всему парку — пользователь мог выйти через
    любой сервер, не только выданный его клиенту)."""
    if pool is None:
        return {}
    try:
        rows = await pool.fetch("SELECT label, server_host, country_code FROM vpn_servers WHERE is_active = TRUE")
    except Exception:
        _LOGGER.warning("connection_check.servers_read_failed")
        return {}
    entries = [
        ((row["label"] or "").strip(), (row["server_host"] or "").strip(), (row["country_code"] or ""))
        for row in rows
        if (row["label"] or "").strip() and (row["server_host"] or "").strip()
    ]
    if not entries:
        return {}
    # Резолвим параллельно: worst-case таймаута не должен складываться по серверам.
    resolved_list = await asyncio.gather(*(_resolve_host_ips(host) for _, host, _ in entries))
    return {
        label: {"ips": ips, "country_code": country}
        for (label, _, country), ips in zip(entries, resolved_list, strict=True)
    }


def match_server_for_ip(client_ip: str, servers_by_label: dict[str, dict[str, Any]]) -> str | None:
    """Метка сервера, чей IP совпал с клиентским; без совпадения — None."""
    if not client_ip:
        return None
    for label, info in servers_by_label.items():
        if client_ip in info["ips"]:
            return label
    return None


def _clamp(value: int | float | None, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, as_int))


async def record_connection_check(
    pool: Any,
    internal_user_id: str,
    *,
    connected: bool,
    server_label: str = "",
    latency_ms: int | None = None,
    throughput_kbps: int | None = None,
    platform: str = "",
) -> bool:
    """Insert результата прогона; сбой аналитики не меняет пользовательский flow.

    connected/server_label определяются сервером по IP запроса — клиентским
    значениям не доверяем. latency/throughput клампятся в границы CHECK колонок.
    """
    if pool is None or not internal_user_id:
        return False
    try:
        await pool.execute(
            "INSERT INTO connection_checks "
            "(internal_user_id, connected, detected_server_label, latency_ms, throughput_kbps, platform) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            internal_user_id,
            connected,
            (server_label or "")[:120],
            _clamp(latency_ms, _LATENCY_MIN_MS, _LATENCY_MAX_MS),
            _clamp(throughput_kbps, _THROUGHPUT_MIN_KBPS, _THROUGHPUT_MAX_KBPS),
            platform,
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        # Не логируем payload: драйвер может включить параметры запроса.
        _LOGGER.warning("connection_check.write_failed")
        return False
