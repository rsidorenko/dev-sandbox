"""Connection Check: IP-детект «трафик идёт через наш VPN», эндпоинты /api/v1/check/*.

Unit-контракты с AsyncMock-pool: приоритет X-Real-IP, матчинг по серверам, клампинг
метрик, fail-open запись, rate limit, кнопка в fast-path клавиатуре бота.
Эндпоинты — через httpx.ASGITransport против build_web_api_app с мок-pool.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.application import connection_check as cc_app
from app.application.connection_check import (
    connection_check_enabled,
    match_server_for_ip,
    real_client_ip,
    record_connection_check,
)
from app.bot_transport.storefront_ui import connect_fastpath_keyboard, connection_check_url
from app.web_api import connection_check as cc_web
from app.web_api.app import build_web_api_app

_SUB_URL = "https://bravada-connect.ru/sub/abcdef1234567890"


# ─── URL кнопки ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (_SUB_URL, "https://bravada-connect.ru/check/abcdef1234567890"),
        ("https://example.com/sub/tok12345678", "https://example.com/check/tok12345678"),
        ("https://example.com/other/tok12345678", None),
        ("https://example.com/sub/short", None),
        ("ftp://example.com/sub/tok12345678", None),
        ("https://example.com/sub/tok/with/slash", None),
    ],
)
def test_connection_check_url_maps_sub_to_check(raw: str, expected: str | None) -> None:
    assert connection_check_url(raw) == expected


def test_fastpath_keyboard_shows_check_button_only_with_flag(monkeypatch) -> None:
    monkeypatch.setenv("CONNECTION_CHECK_ENABLE", "1")
    with_button = connect_fastpath_keyboard("android", _SUB_URL)
    urls_on = [b.get("url") for row in with_button["inline_keyboard"] for b in row if "url" in b]
    assert "https://bravada-connect.ru/check/abcdef1234567890" in urls_on
    callbacks = [b.get("callback_data") for row in with_button["inline_keyboard"] for b in row if "callback_data" in b]
    assert "android_yes" in callbacks  # ручные кнопки не изменились

    monkeypatch.setenv("CONNECTION_CHECK_ENABLE", "0")
    without_button = connect_fastpath_keyboard("android", _SUB_URL)
    urls_off = [b.get("url") for row in without_button["inline_keyboard"] for b in row if "url" in b]
    assert "https://bravada-connect.ru/check/abcdef1234567890" not in urls_off

    monkeypatch.delenv("CONNECTION_CHECK_ENABLE", raising=False)
    assert not connection_check_enabled()


# ─── IP-детект ────────────────────────────────────────────────────────


class _Client:
    def __init__(self, host: str) -> None:
        self.host = host


class _Headers:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def get(self, name: str) -> str | None:
        return self._value if name == "x-real-ip" else None


class _Request:
    def __init__(self, real_ip: str | None, peer: str = "10.0.0.1") -> None:
        self.headers = _Headers(real_ip)
        self.client = _Client(peer)


def test_real_client_ip_prefers_valid_x_real_ip() -> None:
    assert real_client_ip(_Request("203.0.113.7")) == "203.0.113.7"
    assert real_client_ip(_Request("2001:db8::1")) == "2001:db8::1"


def test_real_client_ip_falls_back_on_invalid_header() -> None:
    assert real_client_ip(_Request("not-an-ip")) == "10.0.0.1"
    assert real_client_ip(_Request("203.0.113.7, 10.0.0.1")) == "10.0.0.1"
    assert real_client_ip(_Request(None)) == "10.0.0.1"


def test_match_server_for_ip_matches_and_misses() -> None:
    servers = {"NL-1": {"ips": frozenset({"203.0.113.7"}), "country_code": "NL"}}
    assert match_server_for_ip("203.0.113.7", servers) == "NL-1"
    assert match_server_for_ip("198.51.100.9", servers) is None
    assert match_server_for_ip("", servers) is None


def test_detect_returns_unknown_without_servers() -> None:
    connected, label, country = cc_web._detect(_Request("203.0.113.7"), {})
    assert (connected, label, country) == (None, "", "")


def test_detect_returns_unknown_when_server_map_is_incomplete() -> None:
    servers = {
        "NL-1": {"ips": frozenset({"203.0.113.7"}), "country_code": "NL"},
        "Relay": {"ips": frozenset(), "country_code": "DE"},
    }
    assert cc_web._detect(_Request("203.0.113.7"), servers) == (True, "NL-1", "NL")
    assert cc_web._detect(_Request("198.51.100.9"), servers) == (None, "", "")


def test_resolve_active_server_ips_keeps_unconfigured_rows(monkeypatch) -> None:
    pool = AsyncMock()
    pool.fetch.return_value = [
        {"label": "NL-1", "connection_check_host": "nl.example", "country_code": "NL"},
        {"label": "Relay", "connection_check_host": "", "country_code": "DE"},
    ]

    async def _resolve(host: str) -> frozenset[str]:
        assert host == "nl.example"
        return frozenset({"203.0.113.7"})

    monkeypatch.setattr(cc_app, "_resolve_host_ips", _resolve)
    result = asyncio.run(cc_app.resolve_active_server_ips(pool))
    assert result["NL-1"] == {
        "ips": frozenset({"203.0.113.7"}),
        "country_code": "NL",
        "configured": True,
    }
    assert result["Relay"] == {"ips": frozenset(), "country_code": "DE", "configured": False}
    assert "connection_check_host" in pool.fetch.await_args.args[0]
    assert "server_host" not in pool.fetch.await_args.args[0]


# ─── Запись результата ────────────────────────────────────────────────


def test_record_connection_check_clamps_and_fails_open() -> None:
    pool = AsyncMock()
    assert asyncio.run(
        record_connection_check(
            pool,
            "user-1",
            connected=True,
            server_label="NL-1",
            latency_ms=999_999_999,
            throughput_kbps=-5,
            platform="android",
        )
    )
    sql, *args = pool.execute.await_args.args
    assert sql.startswith("INSERT INTO connection_checks")
    assert args == ["user-1", True, "NL-1", 60000, 0, "android"]

    pool.execute.side_effect = RuntimeError("database unavailable")
    assert not asyncio.run(record_connection_check(pool, "user-1", connected=False))


def test_record_connection_check_rejects_non_numeric_metrics() -> None:
    pool = AsyncMock()
    asyncio.run(record_connection_check(pool, "user-1", connected=False, latency_ms=None, throughput_kbps=None))
    sql, *args = pool.execute.await_args.args
    assert args[3] is None and args[4] is None


# ─── Rate limit ───────────────────────────────────────────────────────


def test_rate_limit_blocks_after_bucket_threshold() -> None:
    for _ in range(30):
        assert cc_web._check_rate_limit("status", "198.51.100.9")
    assert not cc_web._check_rate_limit("status", "198.51.100.9")
    # Другой bucket/IP не пострадал
    assert cc_web._check_rate_limit("status", "198.51.100.10")
    assert cc_web._check_rate_limit("ping", "198.51.100.9")


# ─── Эндпоинты ────────────────────────────────────────────────────────


def _entitled_row() -> dict:
    from datetime import UTC, datetime, timedelta

    return {
        "internal_user_id": "user-1",
        "state_label": "active",
        "trial_expires_at": datetime.now(UTC) + timedelta(days=2),
        "active_until_utc": None,
        "keys_deleted_at": None,
    }


def _client(pool: AsyncMock, **headers: str) -> httpx.AsyncClient:
    app = build_web_api_app(pool=pool)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers)


@pytest.fixture(autouse=True)
def _reset_rate_limit_and_flag(monkeypatch):
    monkeypatch.setenv("CONNECTION_CHECK_ENABLE", "1")
    monkeypatch.setenv("FUNNEL_EVENTS_ENABLE", "1")
    cc_web._rate_limit_store.clear()
    cc_web._rate_last_cleanup = 0.0
    yield
    cc_web._rate_limit_store.clear()


@pytest.fixture
def mock_servers(monkeypatch):
    servers = {
        "NL-1": {"ips": frozenset({"203.0.113.7"}), "country_code": "NL"},
        "DE-2": {"ips": frozenset({"198.51.100.4"}), "country_code": "DE"},
    }

    async def _fake_resolve(pool):
        return servers

    monkeypatch.setattr(cc_web, "resolve_active_server_ips", _fake_resolve)
    return servers


async def test_status_detects_vpn_via_x_real_ip(mock_servers) -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = _entitled_row()
    async with _client(pool, **{"x-real-ip": "203.0.113.7"}) as client:
        res = await client.get("/api/v1/check/abcdef1234567890")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["enabled"] is True
    assert body["connected"] is True
    assert body["server_label"] == "NL-1"
    assert body["server_country"] == "NL"


async def test_status_reports_not_connected_without_match(mock_servers) -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = _entitled_row()
    async with _client(pool, **{"x-real-ip": "192.0.2.50"}) as client:
        res = await client.get("/api/v1/check/abcdef1234567890")
    assert res.json()["connected"] is False


async def test_status_unknown_and_expired_and_not_found(mock_servers) -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = None
    async with _client(pool) as client:
        assert (await client.get("/api/v1/check/unknowntoken00")).status_code == 404

    expired = _entitled_row()
    expired["trial_expires_at"] = None
    pool.fetchrow.return_value = expired
    async with _client(pool) as client:
        assert (await client.get("/api/v1/check/abcdef1234567890")).status_code == 403


async def test_status_disabled_flag_returns_enabled_false(monkeypatch, mock_servers) -> None:
    monkeypatch.setenv("CONNECTION_CHECK_ENABLE", "0")
    pool = AsyncMock()
    async with _client(pool) as client:
        res = await client.get("/api/v1/check/abcdef1234567890")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "enabled": False}


async def test_ping_and_speed_serve_bytes_with_no_store() -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = _entitled_row()
    async with _client(pool) as client:
        ping = await client.get("/api/v1/check/ping")
        assert ping.status_code == 200
        assert ping.headers["cache-control"] == "no-store, max-age=0"
        assert len(ping.content) >= 1

        speed = await client.get("/api/v1/check/abcdef1234567890/speed", params={"bytes": 999_999_999})
        assert speed.status_code == 200
        assert len(speed.content) == 1048576  # кламп до максимума
        assert speed.headers["content-type"].startswith("application/octet-stream")

        tiny = await client.get("/api/v1/check/abcdef1234567890/speed", params={"bytes": 1})
        assert len(tiny.content) == 65536  # кламп до минимума
        assert tiny.content == cc_web._SPEED_PAYLOAD[:65536]
        assert (await client.get("/api/v1/check/speed")).status_code == 404


async def test_speed_disabled_returns_404(monkeypatch) -> None:
    monkeypatch.setenv("CONNECTION_CHECK_ENABLE", "0")
    async with _client(AsyncMock()) as client:
        assert (await client.get("/api/v1/check/abcdef1234567890/speed")).status_code == 404
        assert (await client.get("/api/v1/check/ping")).status_code == 404


async def test_speed_requires_known_active_subscription() -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = None
    async with _client(pool) as client:
        assert (await client.get("/api/v1/check/unknowntoken00/speed")).status_code == 404
        expired = _entitled_row()
        expired["trial_expires_at"] = None
        pool.fetchrow.return_value = expired
        assert (await client.get("/api/v1/check/abcdef1234567890/speed")).status_code == 403


async def test_result_stores_server_side_verdict_with_clamped_metrics(mock_servers) -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = _entitled_row()
    async with _client(pool, **{"x-real-ip": "198.51.100.4"}) as client:
        res = await client.post(
            "/api/v1/check/abcdef1234567890/result",
            json={"latency_ms": 120, "throughput_kbps": 4500, "platform": "android", "connected": True},
        )
    assert res.status_code == 200 and res.json()["ok"] is True
    sql, *args = pool.execute.await_args_list[-1].args
    assert sql.startswith("INSERT INTO connection_checks")
    # connected определяет сервер (True через DE-2), а не клиентский self-report
    assert args == ["user-1", True, "DE-2", 120, 4500, "android"]


async def test_result_does_not_store_unknown_detection(monkeypatch) -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = _entitled_row()

    async def _incomplete_servers(_pool):
        return {"Relay": {"ips": frozenset(), "country_code": "DE", "configured": False}}

    monkeypatch.setattr(cc_web, "resolve_active_server_ips", _incomplete_servers)
    async with _client(pool, **{"x-real-ip": "198.51.100.9"}) as client:
        response = await client.post(
            "/api/v1/check/abcdef1234567890/result",
            json={"latency_ms": 120, "throughput_kbps": 4500, "platform": "android"},
        )
    assert response.json() == {"ok": True, "stored": False, "connected": None}
    assert not any("INSERT INTO connection_checks" in call.args[0] for call in pool.execute.await_args_list)


async def test_result_rejects_bad_payload(mock_servers) -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = _entitled_row()
    async with _client(pool) as client:
        res = await client.post(
            "/api/v1/check/abcdef1234567890/result",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
    assert res.status_code == 400


async def test_feedback_ok_records_funnel_event(mock_servers) -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = _entitled_row()
    async with _client(pool) as client:
        res = await client.post(
            "/api/v1/check/abcdef1234567890/feedback",
            json={"verdict": "ok", "platform": "ios"},
        )
    assert res.status_code == 200
    funnel_calls = [c for c in pool.execute.await_args_list if "funnel_interactions" in c.args[0]]
    assert len(funnel_calls) == 1
    _sql, *args = funnel_calls[0].args
    # (event_name, surface, platform, dedup_key, internal_user_id, telegram_user_id, …)
    assert args[0] == "connection_confirmed"
    assert args[1] == "web" and args[2] == "ios"
    assert args[3].startswith("webcheck:user-1:")


async def test_feedback_problem_records_reason(mock_servers) -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = _entitled_row()
    async with _client(pool) as client:
        res = await client.post(
            "/api/v1/check/abcdef1234567890/feedback",
            json={"verdict": "problem", "reason_code": "connect_failed", "platform": "win"},
        )
    assert res.status_code == 200 and res.json()["feedback_stored"] is True
    feedback_calls = [c for c in pool.execute.await_args_list if "trial_feedback" in c.args[0]]
    assert len(feedback_calls) == 1
    sql, *args = feedback_calls[0].args
    assert args[0] == "connection_problem" and args[1] == "connect_failed" and args[3] == ""
    assert args[4] == "web"


async def test_feedback_rejects_invalid_verdict_and_reason(mock_servers) -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = _entitled_row()
    async with _client(pool) as client:
        bad_verdict = await client.post("/api/v1/check/abcdef1234567890/feedback", json={"verdict": "maybe"})
        bad_reason = await client.post(
            "/api/v1/check/abcdef1234567890/feedback",
            json={"verdict": "ok", "reason_code": "free-text"},
        )
    assert bad_verdict.status_code == 400
    assert bad_reason.status_code == 400
    pool.execute.assert_not_called()


async def test_result_and_feedback_rate_limited(mock_servers) -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = _entitled_row()
    async with _client(pool) as client:
        statuses = [(await client.get("/api/v1/check/abcdef1234567890")).status_code for _ in range(31)]
    assert statuses[-1] == 429
    assert all(s == 200 for s in statuses[:-1])
