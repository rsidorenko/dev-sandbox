"""Tests for sync_clients_table.py — the v3 clients-table desync mirror.

Covers the actual fix: a client present in inbounds.settings JSON but missing
from the v3 `clients` table is mirrored into clients + client_inbounds, the
enable/expiry state is taken from the settings JSON, a repeat run is a no-op,
v2 panels (no clients table) are skipped, and main() gates x-ui restart on
`--restart-if-changed` with the right exit code (10=changed, 0=unchanged).
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_clients_table.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("sync_clients_table", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_v3_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "x-ui.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE inbounds (id INTEGER PRIMARY KEY, port INTEGER, tag TEXT, settings TEXT);
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, sub_id TEXT,
            uuid TEXT, password TEXT, auth TEXT, flow TEXT, security TEXT, reverse TEXT,
            limit_ip INTEGER, total_gb INTEGER, expiry_time INTEGER, enable INTEGER,
            tg_id TEXT, group_name TEXT, comment TEXT, reset INTEGER,
            created_at INTEGER, updated_at INTEGER
        );
        CREATE TABLE client_inbounds (client_id INTEGER, inbound_id INTEGER, flow_override TEXT, created_at INTEGER);
        CREATE TABLE client_traffics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, inbound_id INTEGER, enable INTEGER, email TEXT,
            up INTEGER, down INTEGER, expiry_time INTEGER, total INTEGER, reset INTEGER, last_online INTEGER
        );
        """
    )
    con.commit()
    con.close()
    return db_path


def _add_inbound(db_path: Path, inbound_id: int, clients: list[dict]) -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO inbounds (id, port, tag, settings) VALUES (?, 443, 'in-443-tcp', ?)",
        (inbound_id, json.dumps({"clients": clients})),
    )
    con.commit()
    con.close()


def test_mirrors_missing_client_then_noop(tmp_path: Path) -> None:
    script = _load_script_module()
    db = _make_v3_db(tmp_path)
    _add_inbound(db, 1, [{"id": "uuid-a", "email": "user-aaa", "enable": True, "expiryTime": 0}])

    s1 = script.run_sync(db_path=str(db))
    assert s1["added_clients"] == 1
    assert s1["added_mappings"] == 1

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT uuid, enable FROM clients WHERE email='user-aaa'").fetchone()
    assert row is not None and row["uuid"] == "uuid-a" and row["enable"] == 1
    n = con.execute("SELECT count(*) FROM client_inbounds WHERE inbound_id=1").fetchone()[0]
    assert n == 1
    con.close()

    # second run is a no-op (client already in table + mapped)
    s2 = script.run_sync(db_path=str(db))
    assert s2["added_clients"] == 0 and s2["added_mappings"] == 0 and s2["updated_clients"] == 0


def test_enable_state_taken_from_settings_json(tmp_path: Path) -> None:
    script = _load_script_module()
    db = _make_v3_db(tmp_path)
    _add_inbound(db, 1, [{"id": "uuid-b", "email": "user-bbb", "enable": False, "expiryTime": 123}])
    script.run_sync(db_path=str(db))

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT enable, expiry_time FROM clients WHERE email='user-bbb'").fetchone()
    assert row["enable"] == 0 and row["expiry_time"] == 123
    con.close()


def test_skip_when_no_clients_table(tmp_path: Path) -> None:
    db_path = tmp_path / "x-ui.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE inbounds (id INTEGER PRIMARY KEY, settings TEXT)")
    con.execute(
        "INSERT INTO inbounds (id, settings) VALUES (1, ?)",
        (json.dumps({"clients": [{"id": "x", "email": "y"}]}),),
    )
    con.commit()
    con.close()

    script = _load_script_module()
    s = script.run_sync(db_path=str(db_path))
    assert s == {"added_clients": 0, "updated_clients": 0, "added_mappings": 0,
                 "added_traffics": 0, "updated_traffics": 0, "skipped": 0}


def test_email_collision_reuses_row(tmp_path: Path) -> None:
    script = _load_script_module()
    db = _make_v3_db(tmp_path)
    # pre-existing client row with the email but a different (stale) uuid
    con = sqlite3.connect(db)
    con.execute("INSERT INTO clients (email, uuid, enable) VALUES ('user-ccc', 'uuid-old', 1)")
    con.commit()
    con.close()
    _add_inbound(db, 1, [{"id": "uuid-new", "email": "user-ccc", "enable": True, "expiryTime": 0}])

    s = script.run_sync(db_path=str(db))
    assert s["added_clients"] == 0 and s["updated_clients"] == 1
    con = sqlite3.connect(db)
    row = con.execute("SELECT uuid FROM clients WHERE email='user-ccc'").fetchone()
    assert row[0] == "uuid-new"  # uuid updated in place, no new row
    con.close()


def test_mirrors_client_traffics_enable_and_expiry(tmp_path: Path) -> None:
    """client_traffics.enable gates xray config inclusion. A client disabled in
    client_traffics but enabled in settings JSON must be re-enabled by the sync
    (this is the core fix for the 'keys disabled' bug)."""
    script = _load_script_module()
    db = _make_v3_db(tmp_path)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO client_traffics (inbound_id, enable, email, expiry_time) VALUES (1, 0, 'user-aaa', 111)")
    con.commit(); con.close()
    _add_inbound(db, 1, [{"id": "uuid-a", "email": "user-aaa", "enable": True, "expiryTime": 222}])

    s = script.run_sync(db_path=str(db))
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    row = con.execute("SELECT enable, expiry_time FROM client_traffics WHERE email='user-aaa' AND inbound_id=1").fetchone()
    assert row is not None and row["enable"] == 1 and row["expiry_time"] == 222
    con.close()
    assert s["updated_traffics"] == 1


def test_inserts_client_traffics_when_missing(tmp_path: Path) -> None:
    """A settings client with no client_traffics row gets one inserted (enable + expiry from JSON)."""
    script = _load_script_module()
    db = _make_v3_db(tmp_path)
    _add_inbound(db, 1, [{"id": "uuid-a", "email": "user-aaa", "enable": True, "expiryTime": 555}])

    s = script.run_sync(db_path=str(db))
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    row = con.execute("SELECT enable, expiry_time FROM client_traffics WHERE email='user-aaa'").fetchone()
    assert row is not None and row["enable"] == 1 and row["expiry_time"] == 555
    con.close()
    assert s["added_traffics"] == 1


def test_main_exit_code_and_restart_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    called = {"restart": False}
    monkeypatch.setattr(script, "_restart_xui", lambda: called.__setitem__("restart", True))

    # changed=True, no flag → plain run stays exit 0 (manual workflow must stay
    # green), and does NOT restart
    monkeypatch.setattr(
        script, "run_sync",
        lambda **kw: {"added_clients": 1, "updated_clients": 0, "added_mappings": 0, "skipped": 0},
    )
    called["restart"] = False
    assert script.main([]) == 0
    assert called["restart"] is False

    # changed=True, --restart-if-changed → exit 10, restart called
    called["restart"] = False
    assert script.main(["--restart-if-changed"]) == 10
    assert called["restart"] is True

    # no change → exit 0, no restart even with flag
    monkeypatch.setattr(
        script, "run_sync",
        lambda **kw: {"added_clients": 0, "updated_clients": 0, "added_mappings": 0, "skipped": 5},
    )
    called["restart"] = False
    assert script.main(["--restart-if-changed"]) == 0
    assert called["restart"] is False
