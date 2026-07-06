"""Tests for the h1cloud h1-fork integration: the h1 client schema (build_h1_client) and the
SFTP table-sync reconcile (_sync_h1_tables + _reconcile_h1_server). The h1-fork reads clients from
the clients/client_inbounds/client_traffics tables (NOT settings.clients JSON), so the bot syncs the
tables directly via SFTP db-edit (never /update, which desyncs + loops)."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock

from app.issuance.xui_client import XuiServerConfig
from app.issuance.xui_vless_provider import (
    XuiVlessProvider,
    _email_from_internal,
    _sync_h1_tables,
    build_h1_client,
)


def test_build_h1_client_shape() -> None:
    c = build_h1_client(user_uuid="the-uuid", email="x-user-u1")
    assert c["id"] == "the-uuid"
    assert c["email"] == "x-user-u1"
    assert c["enable"] is True
    assert c["expiryTime"] == 0 and c["limitIp"] == 0 and c["totalGB"] == 0
    assert c["tgId"] == 0
    assert "flow" not in c
    assert c["security"] == "auto"
    for f in ("subId", "auth", "password"):
        v = c[f]
        assert isinstance(v, str) and len(v) == 16 and v.isalnum()


def test_build_h1_client_unique_tokens() -> None:
    a = build_h1_client(user_uuid="U", email="e")
    b = build_h1_client(user_uuid="U", email="e")
    assert a["subId"] != b["subId"]


def test_build_h1_client_enable_param_drives_enable_field() -> None:
    assert build_h1_client(user_uuid="U", email="e")["enable"] is True
    assert build_h1_client(user_uuid="U", email="e", enable=False)["enable"] is False


# --- _sync_h1_tables (the core: desired-state into the 3 tables, scoped to x-user-) ---


def _h1_test_db(seed_manual: bool = True) -> sqlite3.Connection:
    """Build a temp sqlite db with the h1-fork client-table schema + optional manual client."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        """CREATE TABLE clients (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
        sub_id TEXT, uuid TEXT, password TEXT, auth TEXT, flow TEXT, security TEXT, reverse TEXT,
        limit_ip INTEGER, total_gb INTEGER, expiry_time INTEGER, enable NUMERIC, tg_id INTEGER,
        group_name TEXT, comment TEXT, reset INTEGER, created_at INTEGER, updated_at INTEGER,
        wg_private_key TEXT, wg_public_key TEXT, wg_allowed_ips TEXT, wg_pre_shared_key TEXT,
        wg_keep_alive INTEGER)"""
    )
    c.execute(
        "CREATE TABLE client_inbounds (client_id INTEGER, inbound_id INTEGER, flow_override TEXT, created_at INTEGER)"
    )
    c.execute(
        """CREATE TABLE client_traffics (id INTEGER PRIMARY KEY AUTOINCREMENT, inbound_id INTEGER,
        enable NUMERIC, email TEXT, up INTEGER, down INTEGER, expiry_time INTEGER, total INTEGER,
        reset INTEGER, last_online INTEGER)"""
    )
    c.execute("CREATE TABLE inbounds (id INTEGER PRIMARY KEY, settings TEXT)")
    c.execute(
        "INSERT INTO inbounds (id, settings) VALUES (1, ?)",
        (json.dumps({"clients": []}),),
    )
    if seed_manual:
        c.execute(
            """INSERT INTO clients (email, sub_id, uuid, password, auth, flow, security, reverse,
            limit_ip, total_gb, expiry_time, enable, tg_id, group_name, comment, reset, created_at,
            updated_at) VALUES ('manual-test','s','MANUAL-UUID','p','a','',  'auto','', 0,0,0,1,0,'','',0,1,1)"""
        )
        mid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO client_inbounds VALUES (?,?,?,1)", (mid, 1, ""))
        c.execute("INSERT INTO client_traffics (inbound_id, enable, email) VALUES (1,1,'manual-test')")
        c.execute(
            "UPDATE inbounds SET settings=? WHERE id=1",
            (json.dumps({"clients": [{"id": "MANUAL-UUID", "email": "manual-test", "enable": True}]}),),
        )
    conn.commit()
    return conn


def test_sync_h1_tables_adds_new_bot_clients() -> None:
    conn = _h1_test_db()
    desired = {
        "x-user-u1": {"uuid": "UUID1", "enable": True, "expiry": 111},
        "x-user-u2": {"uuid": "UUID2", "enable": False, "expiry": 222},
    }
    changed = _sync_h1_tables(conn, desired, inbound_id=1)
    assert changed is True
    rows = {r["email"]: r for r in conn.execute("SELECT email, uuid, enable, expiry_time FROM clients")}
    assert set(rows) == {"manual-test", "x-user-u1", "x-user-u2"}  # manual kept + 2 added
    assert rows["x-user-u1"]["enable"] == 1 and rows["x-user-u1"]["expiry_time"] == 111
    assert rows["x-user-u2"]["enable"] == 0 and rows["x-user-u2"]["expiry_time"] == 222
    # client_inbounds + client_traffics populated for the 2 new
    assert conn.execute("SELECT count(*) FROM client_inbounds").fetchone()[0] == 3  # manual + 2
    assert conn.execute("SELECT count(*) FROM client_traffics").fetchone()[0] == 3
    # settings.clients JSON has them
    se = json.loads(conn.execute("SELECT settings FROM inbounds").fetchone()["settings"])
    emails = {cl["email"] for cl in se["clients"]}
    assert {"manual-test", "x-user-u1", "x-user-u2"} <= emails
    conn.close()


def test_sync_h1_tables_updates_enable_and_expiry() -> None:
    conn = _h1_test_db()
    _sync_h1_tables(conn, {"x-user-u1": {"uuid": "U1", "enable": True, "expiry": 100}}, 1)
    # now flip to disabled + new expiry
    changed = _sync_h1_tables(conn, {"x-user-u1": {"uuid": "U1", "enable": False, "expiry": 999}}, 1)
    assert changed is True
    r = conn.execute("SELECT enable, expiry_time FROM clients WHERE email='x-user-u1'").fetchone()
    assert r["enable"] == 0 and r["expiry_time"] == 999
    t = conn.execute("SELECT enable, expiry_time FROM client_traffics WHERE email='x-user-u1'").fetchone()
    assert t["enable"] == 0 and t["expiry_time"] == 999
    conn.close()


def test_sync_h1_tables_removes_orphan_bot_clients() -> None:
    conn = _h1_test_db()
    _sync_h1_tables(conn, {"x-user-u1": {"uuid": "U1", "enable": True, "expiry": 1}}, 1)
    # u1 no longer desired -> removed; manual kept
    changed = _sync_h1_tables(conn, {}, 1)
    assert changed is True
    emails = {r["email"] for r in conn.execute("SELECT email FROM clients")}
    assert emails == {"manual-test"}  # only manual survives
    assert conn.execute("SELECT count(*) FROM client_traffics WHERE email='x-user-u1'").fetchone()[0] == 0
    conn.close()


def test_sync_h1_tables_never_touches_manual_clients() -> None:
    conn = _h1_test_db()
    _sync_h1_tables(conn, {"x-user-u1": {"uuid": "U1", "enable": True, "expiry": 1}}, 1)
    # a desired that does NOT include manual-test must not delete it
    _sync_h1_tables(conn, {}, 1)
    assert conn.execute("SELECT count(*) FROM clients WHERE email='manual-test'").fetchone()[0] == 1
    conn.close()


def test_sync_h1_tables_steady_state_no_change() -> None:
    conn = _h1_test_db()
    desired = {"x-user-u1": {"uuid": "U1", "enable": True, "expiry": 100}}
    _sync_h1_tables(conn, desired, 1)
    changed = _sync_h1_tables(conn, desired, 1)  # same desired -> no change
    assert changed is False
    conn.close()


# --- _reconcile_h1_server graceful skip when SFTP creds are absent ---


def _provider() -> XuiVlessProvider:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    return p


def test_reconcile_h1_skips_without_sftp_creds(monkeypatch) -> None:
    """No H1_SFTP_HOST -> graceful skip (no SFTP, no /update, h1cloud untouched)."""
    monkeypatch.delenv("H1_SFTP_HOST", raising=False)
    cfg = XuiServerConfig(
        server_id=22,
        label="h1cloud",
        country_code="NL",
        country_flag="🇳🇱",
        server_host="node.rkn-pidori.shop",
        server_port=443,
        ws_path="api/v1/bs1961/sync",
        tls_sni="node.rkn-pidori.shop",
        panel_url="http://193.233.126.96:25100/x",
        panel_username="u",
        panel_password="p",
        inbound_id=1,
        transport_type="xhttp",
        provider_kind="xui",
        client_surface="happ",
        panel_flavor="h1",
    )
    m = AsyncMock()
    m.server_config = cfg
    m.server_id = 22
    users = [{"internal_user_id": "u1", "state_label": "active", "active_until_utc": None}]

    from app.shared.test_helpers import run_async as _run

    _run(_provider()._reconcile_h1_server(m, users))
    # no SFTP/panel methods called (graceful skip)
    m.restart_xray.assert_not_awaited()


def test_email_from_internal_xhttp_prefix() -> None:
    assert _email_from_internal("u1", transport_type="xhttp") == "x-user-u1"
    assert _email_from_internal("u1", transport_type="tcp") == "user-u1"
