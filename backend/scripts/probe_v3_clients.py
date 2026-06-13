#!/usr/bin/env python3
"""READ-ONLY diagnostic: is this 3x-ui server's v3 `clients` table in sync with
the bot-provisioned `inbounds.settings` JSON?

ROOT CAUSE THIS PROBES (see sync_clients_table.py):
  - 3x-ui v3.x generates xray's client list from the `clients` + `client_inbounds`
    DB tables, NOT from `inbounds.settings.clients` JSON.
  - The bot provisions clients via /panel/api/inbounds/update, which writes ONLY
    the JSON. Nothing mirrors JSON → tables automatically.
  - Symptom: user connects, Reality handshake passes, then the VLESS connection is
    rejected because xray (reading the table) doesn't recognize the UUID.

This prints, per server (no writes):
  - DB path + whether the v3 `clients` table exists (v2 vs v3).
  - total `clients`, total `client_inbounds`, per-inbound mapping counts.
  - for SAMPLE_UUID (env): present in `clients`? linked to which inbound(s)?
    AND present in which inbound's settings JSON?  → reveals JSON-vs-table gap.
  - xray running? :443 / :8443 listening?
  - per inbound: id, port, protocol, security, reality publicKey/shortIds/
    serverNames/dest, and settings.clients JSON count (to compare reality params
    against the issued link, ruling out stale-pbk).

Run on the server (ssh in, then):  SAMPLE_UUID=<uuid> python3 probe_v3_clients.py
Idempotent / read-only — safe to run any time.
"""
import json
import os
import sqlite3
import subprocess

DB_CANDIDATES = (
    "/etc/x-ui/x-ui.db",
    "/usr/local/x-ui/x-ui.db",
    "/opt/x-ui/x-ui.db",
    "/root/x-ui/x-ui.db",
)

SAMPLE_UUID = (os.environ.get("SAMPLE_UUID") or "").strip().lower()


def _find_db():
    for p in DB_CANDIDATES:
        if os.path.exists(p):
            return p
    # fall back to a shallow search
    try:
        out = subprocess.run(
            ["find", "/etc", "/usr/local", "/opt", "/root", "-maxdepth", "3",
             "-name", "x-ui.db", "-type", "f"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip().splitlines()
        if out:
            return out[0]
    except Exception:
        pass
    return None


def _proc(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "(cmd failed)"


def main():
    print("=" * 64)
    print("V3 CLIENTS-TABLE PROBE (read-only)")
    print("=" * 64)

    # ── xray + ports ──
    xray_pid = _proc("pgrep -f 'xray' | head -3")
    print(f"\nxray pids: {xray_pid or 'NONE (xray NOT running)'}")
    for port in (443, 8443, 2053, 1864, 10443):
        lst = _proc(f"ss -tlnp 2>/dev/null | grep ':{port} ' | head -2")
        print(f"  :{port} listening: {lst or 'NO'}")

    # ── DB ──
    db = _find_db()
    if not db:
        print("\n!! No x-ui.db found — is 3x-ui installed here?")
        return
    print(f"\nx-ui DB: {db}")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    c = con.cursor()

    has_clients = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='clients'"
    ).fetchone()
    if not has_clients:
        print("\nNO `clients` table → this is 3x-ui v2.x (reads JSON directly). "
              "v3-sync gap does NOT apply here; look elsewhere.")
        _dump_inbounds_json_only(c)
        con.close()
        return

    print("v3 `clients` table: PRESENT (v3.x — xray reads this, not JSON)")
    n_clients = c.execute("SELECT count(*) FROM clients").fetchone()[0]
    n_maps = c.execute("SELECT count(*) FROM client_inbounds").fetchone()[0]
    print(f"  total clients rows: {n_clients}")
    print(f"  total client_inbounds mappings: {n_maps}")

    print("\n--- per-inbound (client_inbounds) ---")
    for r in c.execute(
        "SELECT inbound_id, count(*) AS cnt FROM client_inbounds GROUP BY inbound_id ORDER BY inbound_id"
    ):
        print(f"  inbound {r['inbound_id']}: {r['cnt']} mapped clients")

    # ── SAMPLE_UUID: table vs JSON ──
    if SAMPLE_UUID:
        print(f"\n--- SAMPLE_UUID {SAMPLE_UUID[:8]}... ---")
        row = c.execute("SELECT id, email, enable FROM clients WHERE uuid=?", (SAMPLE_UUID,)).fetchone()
        if row:
            links = [r2["inbound_id"] for r2 in c.execute(
                "SELECT inbound_id FROM client_inbounds WHERE client_id=?", (row["id"],))]
            print(f"  IN `clients` table: YES (id={row['id']}, enable={row['enable']}, "
                  f"linked inbounds={links})")
        else:
            print("  IN `clients` table: *** NO ***  ← if JSON has it, THIS is the breakage")

        print("  in settings JSON per inbound:")
        for ib in c.execute("SELECT id, port, settings FROM inbounds"):
            try:
                cl = json.loads(ib["settings"]).get("clients", [])
            except Exception:
                continue
            ids = [x.get("id", "").lower() for x in cl]
            if SAMPLE_UUID in ids:
                print(f"    inbound {ib['id']} (:{ib['port']}): PRESENT in JSON "
                      f"({len(ids)} clients total)")
        # summary verdict
        in_table = bool(row)
        print(f"  VERDICT: table={in_table}  → "
              + ("OK (recognized by xray)" if in_table
                 else "GAP — JSON has it but table does not; run sync_clients_table.py"))
    else:
        print("\n(set SAMPLE_UUID env to check a specific user)")

    _dump_inbounds_json_only(c)
    con.close()


def _xray_pubkey(privkey: str) -> str:
    """Derive the Reality publicKey from the panel's privateKey via xray x25519 -i.
    3x-ui stores privateKey in streamSettings.realitySettings; the client-link pbk
    is the matching publicKey. A mismatch means the link's pbk is stale → handshake
    fails. Returns the derived publicKey or an error string."""
    if not privkey:
        return "(no privateKey in panel)"
    for binary in (
        "/usr/local/x-ui/bin/xray-linux-amd64",
        "/usr/local/x-ui/bin/xray",
        "/usr/local/bin/xray",
        "xray",
    ):
        try:
            r = subprocess.run(
                [binary, "x25519", "-i", privkey],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    if line.strip().startswith("Public"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            continue
    return "(xray binary not found / derive failed)"


def _dump_inbounds_json_only(c):
    print("\n--- inbounds: reality params + JSON client counts ---")
    rows = c.execute("SELECT id, port, protocol, settings, stream_settings FROM inbounds ORDER BY id").fetchall()
    if not rows:
        print("  (no inbounds)")
        return
    for ib in rows:
        try:
            settings = json.loads(ib["settings"]) if ib["settings"] else {}
        except Exception:
            settings = {}
        n_json = len(settings.get("clients", []))
        try:
            ss = json.loads(ib["stream_settings"]) if ib["stream_settings"] else {}
        except Exception:
            ss = {}
        sec = ss.get("security", "?")
        rs = ss.get("realitySettings", {}) or {}
        line = (f"  inbound {ib['id']} :{ib['port']} proto={ib['protocol']} "
                f"sec={sec} json_clients={n_json}")
        if sec == "reality":
            priv = rs.get("privateKey") or rs.get("settings", {}).get("privateKey") or ""
            sids = rs.get("shortIds") or rs.get("settings", {}).get("shortIds") or []
            snis = rs.get("serverNames") or rs.get("settings", {}).get("serverNames") or []
            dest = rs.get("dest") or rs.get("settings", {}).get("dest") or "?"
            derived_pbk = _xray_pubkey(priv)
            line += (f"\n      reality derived_pbk={derived_pbk}  shortIds={sids} "
                     f"serverNames={snis} dest={dest}")
            line += f"\n      (compare derived_pbk to the issued link's pbk= — mismatch = stale key = handshake fails)"
        print(line)


if __name__ == "__main__":
    main()
