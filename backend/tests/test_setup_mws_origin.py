"""Unit tests for the pure helpers in setup_mws_origin.py (MWS CDN origin inbound)."""

import importlib.util
import json
import os
from pathlib import Path


def _load_module():
    """Load setup_mws_origin.py as a module (it lives in scripts/, not a package)."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "setup_mws_origin.py"
    spec = importlib.util.spec_from_file_location("setup_mws_origin", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ws_inbound_stream_settings_shape():
    mod = _load_module()
    ss = json.loads(mod.ws_inbound_stream_settings())
    assert ss["network"] == "ws"
    assert ss["security"] == "none"  # MWS edge does TLS; origin is plain WS
    assert ss["wsSettings"]["path"] == "/mws"
    assert ss["wsSettings"]["acceptProxyProtocol"] is False
    assert ss["wsSettings"]["host"] == ""


def test_ws_path_env_overrides(monkeypatch):
    """MWS_PATH env at import time drives the ws path in streamSettings."""
    monkeypatch.setenv("MWS_PATH", "/custom-path/")
    path = Path(__file__).resolve().parents[1] / "scripts" / "setup_mws_origin.py"
    spec = importlib.util.spec_from_file_location("setup_mws_origin_env", path)
    mod2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod2)
    ss = json.loads(mod2.ws_inbound_stream_settings())
    assert ss["wsSettings"]["path"] == "/custom-path/"


def test_create_inbound_inserts_test_client(tmp_path):
    """create_inbound_with_test_client builds a valid inbounds row with the test client."""
    import sqlite3

    mod = _load_module()
    db = tmp_path / "x-ui.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE inbounds (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INT, up INT, "
        "down INT, total INT, remark TEXT, enable INT, expiry_time INT, listen TEXT, port INT, "
        "protocol TEXT, settings TEXT, stream_settings TEXT, tag TEXT, sniffing TEXT)"
    )
    conn.commit()
    conn.close()

    inbound_id = mod.create_inbound_with_test_client(str(db))
    assert inbound_id > 0
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT port, protocol, settings, stream_settings, tag FROM inbounds WHERE id=?",
        (inbound_id,),
    ).fetchone()
    conn.close()
    assert row[0] == mod.MWS_PORT
    assert row[1] == "vless"
    assert row[4] == "in-mws-ws"
    settings = json.loads(row[2])
    assert len(settings["clients"]) == 1
    assert settings["clients"][0]["id"] == mod.TEST_UUID
    stream = json.loads(row[3])
    assert stream["network"] == "ws"
    assert stream["security"] == "none"


def test_create_inbound_is_idempotent(tmp_path):
    """Second call replaces the inbound on the same port/tag (no duplicate)."""
    import sqlite3

    mod = _load_module()
    db = tmp_path / "x-ui.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE inbounds (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INT, up INT, "
        "down INT, total INT, remark TEXT, enable INT, expiry_time INT, listen TEXT, port INT, "
        "protocol TEXT, settings TEXT, stream_settings TEXT, tag TEXT, sniffing TEXT)"
    )
    conn.commit()
    conn.close()

    first = mod.create_inbound_with_test_client(str(db))
    second = mod.create_inbound_with_test_client(str(db))
    # Idempotent = no duplicate rows on the same port/tag (delete+insert replaces).
    # (The inbound id may change on re-run via AUTOINCREMENT; that's fine — re-running
    # setup is a one-shot op, and add_mws_row is re-run with the new INBOUND_ID.)
    conn = sqlite3.connect(db)
    by_port = conn.execute("SELECT count(*) FROM inbounds WHERE port=?", (mod.MWS_PORT,)).fetchone()[0]
    by_tag = conn.execute("SELECT count(*) FROM inbounds WHERE tag=?", ("in-mws-ws",)).fetchone()[0]
    conn.close()
    assert by_port == 1
    assert by_tag == 1
