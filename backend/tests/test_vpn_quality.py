"""Измеритель не использует клиентов, не обходит SOCKS и не продлевает grant повторно."""

import io
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from app.observability import vpn_quality as quality
from app.observability import vpn_quality_probe as probe
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.app import build_web_api_app


def trace(ip="192.0.2.1", **overrides):
    data = {
        "http_code": 200,
        "size_download": 150,
        "time_appconnect": 0.1,
        "time_starttransfer": 0.2,
        "time_total": 0.21,
        **overrides,
    }
    return f"ip={ip}\nsecret-body-canary\n" + json.dumps(data)


@pytest.mark.parametrize(
    ("rc", "expected", "result"),
    [
        (0, ["192.0.2.1"], "ok"),
        (0, [], "route_unknown"),
        (0, ["192.0.2.2"], "route_mismatch"),
        (28, [], "timeout"),
        (60, [], "tls_error"),
        (7, [], "connect_error"),
    ],
)
def test_interpret_does_not_return_body_ip_or_credentials(rc, expected, result):
    value = probe.interpret(rc, trace(), expected)
    assert value["result"] == result and value["tls_ms"] == 100
    assert "secret" not in json.dumps(value) and "192.0.2" not in json.dumps(value)


@pytest.mark.parametrize(
    "overrides", [{"time_total": float("nan")}, {"time_appconnect": -1}, {"size_download": 99999}, {"http_code": 999}]
)
def test_invalid_metrics_fail_closed(overrides):
    assert probe.interpret(0, trace(**overrides), ["192.0.2.1"])["result"] == "measurement_error"


def test_http_failure_is_not_a_successful_speed_or_tunnel_check():
    assert probe.interpret(0, trace(http_code=403), ["192.0.2.1"])["result"] == "http_error"
    assert probe.interpret(0, trace(ip="invalid"), ["192.0.2.1"])["result"] == "invalid_response"


def test_link_supports_tls_xhttp_and_never_insecure():
    link = "vless://00000000-0000-4000-8000-000000000001@entry.example:443?type=xhttp&security=tls&sni=entry.example&path=%2Ftest&extra=%7B%22scMaxEachPostBytes%22%3A1000%7D"
    out = probe.outbound_from_link(link)
    assert out["streamSettings"]["tlsSettings"]["allowInsecure"] is False
    assert out["streamSettings"]["xhttpSettings"]["extra"]["scMaxEachPostBytes"] == 1000
    with pytest.raises(ValueError):
        probe.outbound_from_link(link.replace("security=tls", "security=none"))


def test_probe_requires_socks_despite_no_proxy_and_cleans_up(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    process = MagicMock(pid=12345)
    process.poll.return_value = None
    process.stdin = MagicMock()
    start = MagicMock(return_value=process)
    request = MagicMock(return_value=SimpleNamespace(returncode=0, stdout=trace()))
    cleanup = MagicMock()
    monkeypatch.setattr(probe.subprocess, "Popen", start)
    monkeypatch.setattr(probe.subprocess, "run", request)
    monkeypatch.setattr(probe.socket, "create_connection", MagicMock())
    monkeypatch.setattr(probe, "terminate", cleanup)
    item = {"outbound": {"protocol": "vless", "secret": "credential-canary"}, "expected_ips": ["192.0.2.1"]}
    assert probe.probe(item)["result"] == "ok"
    args = request.call_args.args[0]
    assert args[args.index("--noproxy") + 1] == "" and "--socks5-hostname" in args
    assert "credential-canary" not in repr(start.call_args) and "stdin:" in start.call_args.args[0]
    assert start.call_args.args[0][:4] == ["/usr/bin/timeout", "-k", "2", "20"]
    assert "credential-canary" in process.stdin.write.call_args.args[0].decode()
    cleanup.assert_called_once_with(process)
    request.side_effect = probe.MeasurementDeadline()
    with pytest.raises(probe.MeasurementDeadline):
        probe.probe(item)
    assert cleanup.call_count == 2


def test_whole_worker_deadline_stops_remaining_profiles(monkeypatch):
    monkeypatch.setattr(probe.signal, "signal", MagicMock())
    monkeypatch.setattr(probe.signal, "alarm", MagicMock())
    monkeypatch.setattr(probe.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(probe.sys, "stdin", io.StringIO(json.dumps([{}, {}])))
    operation = MagicMock(side_effect=probe.MeasurementDeadline())
    monkeypatch.setattr(probe, "probe", operation)
    assert probe.main() == 1 and operation.call_count == 1


def account(**overrides):
    return {
        "enabled": True,
        "is_internal": True,
        "telegram_user_id": -1,
        "vless_rotation_pending": False,
        "state_label": "active",
        "active_until_utc": datetime.now(UTC) + timedelta(days=1),
        "keys_deleted_at": None,
        "keys_deactivated_at": None,
        **overrides,
    }


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"enabled": False}, "disabled"),
        ({"is_internal": False}, "invalid_identity"),
        ({"telegram_user_id": 1}, "invalid_identity"),
        ({"vless_rotation_pending": True}, "rotation_pending"),
        ({"active_until_utc": None}, "expired"),
        ({"keys_deleted_at": datetime.now(UTC)}, "inactive_keys"),
    ],
)
def test_no_fallback_to_customer_identity(changes, reason):
    assert quality.ineligible_reason(account(**changes)) == reason


@pytest.mark.asyncio
async def test_incomplete_auto_exit_map_is_unknown(monkeypatch):
    config = SimpleNamespace(
        server_id=1,
        label="Профиль",
        transport_type="tcp",
        auto_selection_route=65001,
        auto_selection_label="Авто",
        auto_selection_country_flag="x",
    )
    provider = SimpleNamespace(
        _get_bare_xray_clients=AsyncMock(return_value=[]),
        _bare_xray_configs=[config],
        _uuid_for_transport=AsyncMock(return_value="00000000-0000-4000-8000-000000000001"),
    )
    pool = SimpleNamespace(
        fetch=AsyncMock(
            return_value=[
                {"id": 1, "connection_check_host": "first.example"},
                {"id": 2, "connection_check_host": "failed.example"},
            ]
        )
    )
    monkeypatch.setattr(
        quality,
        "_resolve_host_ips",
        AsyncMock(side_effect=lambda host: {"192.0.2.1"} if host == "first.example" else set()),
    )
    monkeypatch.setattr(quality, "_build_vless_link", lambda *_: "unused")
    monkeypatch.setattr(probe, "outbound_from_link", lambda _: {"protocol": "vless"})
    _, items = await quality.profiles(pool, provider, "service")
    assert items[0]["expected_ips"] == ["192.0.2.1"] and items[1]["expected_ips"] == []
    assert items[0]["route"] == 0 and items[1]["route"] == 65001


@pytest.fixture
async def db():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("Нужен изолированный PostgreSQL")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
    await pool.execute(
        "INSERT INTO vpn_servers(id,label,country_code,country_flag,server_host,panel_url,panel_username,panel_password,provider_kind) "
        "VALUES(991050,'Тест','DE','','test.example','panel-canary','user-canary','password-canary','bare_xray') ON CONFLICT(id) DO NOTHING"
    )
    yield pool
    ids = await pool.fetch("SELECT internal_user_id FROM vpn_quality_accounts WHERE location LIKE 'test-quality-%'")
    await pool.execute(
        "DELETE FROM vpn_quality_samples WHERE run_id IN (SELECT id FROM vpn_quality_runs WHERE location LIKE 'test-quality-%')"
    )
    await pool.execute("DELETE FROM vpn_quality_runs WHERE location LIKE 'test-quality-%'")
    await pool.execute("DELETE FROM vpn_quality_accounts WHERE location LIKE 'test-quality-%'")
    for row in ids:
        identity = row["internal_user_id"]
        for table in [
            "analytics_internal_audit",
            "admin_subscription_actions",
            "subscription_snapshots",
            "user_identities",
        ]:
            await pool.execute(f"DELETE FROM {table} WHERE internal_user_id=$1", identity)
    await pool.execute("DELETE FROM vpn_servers WHERE id=991050")
    await pool.close()


@pytest.mark.asyncio
async def test_bootstrap_dry_run_race_and_crash_retry(db):
    location = "test-quality-bootstrap"
    before = await db.fetchval("SELECT COUNT(*) FROM user_identities")
    assert await quality.bootstrap(db, location=location, entry_server_id=991050) is None
    assert await db.fetchval("SELECT COUNT(*) FROM user_identities") == before
    import asyncio

    a, b = await asyncio.gather(
        *(quality.bootstrap(db, location=location, entry_server_id=991050, apply=True) for _ in range(2))
    )
    assert a["internal_user_id"] == b["internal_user_id"]
    state = await quality.account_state(db, location)
    assert state["is_internal"] and state["telegram_user_id"] < 0 and not state["enabled"]
    expires = state["active_until_utc"]
    # Повтор после commit, но до provisioning: тот же аккаунт, без нового grant.
    await quality.bootstrap(db, location=location, entry_server_id=991050, apply=True)
    assert (await quality.account_state(db, location))["active_until_utc"] == expires
    assert (
        await db.fetchval(
            "SELECT COUNT(*) FROM admin_subscription_actions WHERE internal_user_id=$1", a["internal_user_id"]
        )
        == 1
    )
    assert await quality.collect(db, object(), location) == "disabled"
    with pytest.raises(ValueError, match="location_entry_conflict"):
        await quality.bootstrap(db, location=location, entry_server_id=991051, apply=True)


@pytest.mark.asyncio
async def test_bootstrap_failure_rolls_back_registry_and_identity(db, monkeypatch):
    before = await db.fetchval("SELECT COUNT(*) FROM user_identities")
    monkeypatch.setattr(quality, "grant_subscription_days", AsyncMock(side_effect=RuntimeError("test")))
    with pytest.raises(RuntimeError):
        await quality.bootstrap(db, location="test-quality-rollback", entry_server_id=991050, apply=True)
    assert await db.fetchval("SELECT COUNT(*) FROM user_identities") == before
    assert await db.fetchval("SELECT COUNT(*) FROM vpn_quality_accounts WHERE location='test-quality-rollback'") == 0


@pytest.mark.asyncio
async def test_collection_history_and_admin_auth(db, monkeypatch):
    location = "test-quality-api"
    await quality.bootstrap(db, location=location, entry_server_id=991050, apply=True)
    await db.execute("UPDATE vpn_quality_accounts SET enabled=TRUE WHERE location=$1", location)
    item = {"server_id": 991050, "route": 0, "label": "Тест", "outbound": {"secret": "credential-canary"}}
    monkeypatch.setattr(quality, "profiles", AsyncMock(return_value=([SimpleNamespace(server_id=991050)], [item])))
    monkeypatch.setattr(
        quality,
        "remote_probe",
        lambda *_: ([{"server_id": 991050, "route": 0, **probe.interpret(0, trace(), ["192.0.2.1"])}], 0),
    )
    assert await quality.collect(db, object(), location) == "complete"
    for key, value in {
        "WEB_ADMIN_ENABLE": "1",
        "WEB_ADMIN_USERNAME": "admin",
        "WEB_ADMIN_PASSWORD": "test-password",
        "JWT_SECRET": "test-secret",
        "WEB_API_DEV_INSECURE_COOKIE": "1",
    }.items():
        monkeypatch.setenv(key, value)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_web_api_app(pool=db)), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/admin/vpn-quality")).status_code == 401
        assert (
            await client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "test-password"})
        ).status_code == 200
        response = await client.get("/api/v1/admin/vpn-quality")
        assert response.status_code == 200 and "canary" not in response.text and "internal_user_id" not in response.text
        row = next(r for r in response.json()["profiles"] if r["location"] == location)
        assert row["attempts"] == row["verified"] == 1 and row["p50_ms"] == 200
        monkeypatch.setattr(quality, "remote_probe", lambda *_: ([], 1))
        assert await quality.collect(db, object(), location) == "measurement_error"
        response = await client.get("/api/v1/admin/vpn-quality")
        point = next(r for r in response.json()["locations"] if r["location"] == location)
        assert point["status"] == "measurement_error"
