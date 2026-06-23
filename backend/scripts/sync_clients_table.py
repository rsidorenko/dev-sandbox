#!/usr/bin/env python3
"""Sync inbounds.settings clients → clients + client_inbounds tables.

3x-ui v3.x generates xray config.json from a separate `clients` table.
The bot's API (/panel/api/inbounds/update) writes only to inbounds.settings JSON,
so clients added/changed by the bot never reach the `clients` table and xray
rejects them ("invalid request user id") until this script mirrors them.

Run on the VPN server itself: python3 sync_clients_table.py

For periodic / unattended use, pass --restart-if-changed so x-ui is restarted
ONLY when the table actually changed (x-ui does not detect direct SQLite writes,
so a restart is required for xray to regenerate config.json from the table).
A systemd timer can run this every few minutes with near-zero impact when idle
(see sync-clients-table.timer / install_sync_timer.sh).

Exit code: plain runs always exit 0 (keeps the manual sync workflow green even
when there are pending changes). With --restart-if-changed: 10 = table changed
(and x-ui restarted), 0 = no change. The marker line
`SYNC_RESULT changed=<0|1> ...` is always printed last for machine parsing.
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import time

DB_PATH = "/etc/x-ui/x-ui.db"


def run_sync(db_path: str = DB_PATH) -> dict:
    """Mirror inbounds.settings clients into clients + client_inbounds tables.

    Returns a stats dict: added_clients, updated_clients, added_mappings, skipped.
    ``db_path`` defaults to the production panel DB; tests pass a temp path.
    """
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    c = db.cursor()

    # Check if clients table exists (v3.x only)
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='clients'")
    if not c.fetchone():
        print("SKIP: No 'clients' table (3x-ui v2.x — uses inbounds.settings directly)")
        db.close()
        return {"added_clients": 0, "updated_clients": 0, "added_mappings": 0,
                "added_traffics": 0, "updated_traffics": 0, "skipped": 0}

    now_ms = int(time.time() * 1000)

    # Get existing UUIDs → client id mapping
    c.execute("SELECT id, uuid FROM clients")
    existing_clients = {}
    for row in c.fetchall():
        existing_clients[row["uuid"]] = row["id"]

    # Get existing client_inbounds mappings
    c.execute("SELECT client_id, inbound_id FROM client_inbounds")
    existing_mappings = set()
    for row in c.fetchall():
        existing_mappings.add((row["client_id"], row["inbound_id"]))

    # client_traffics is the table x-ui v3 reads to generate xray config.json — a
    # client with client_traffics.enable=0 is EXCLUDED from config even when it is
    # enable=1 in `clients` + settings JSON + client_inbounds. So mirror enable +
    # expiry here too (this is the fix for the "keys disabled / stale expiry" bug).
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='client_traffics'")
    has_traffics = c.fetchone() is not None
    existing_traffics: dict[tuple, tuple] = {}  # (email, inbound_id) -> (id, enable, expiry_time)
    if has_traffics:
        for row in c.execute("SELECT id, inbound_id, email, enable, expiry_time FROM client_traffics"):
            existing_traffics[(row["email"], row["inbound_id"])] = (row["id"], row["enable"], row["expiry_time"])

    added_clients = 0
    updated_clients = 0
    added_mappings = 0
    added_traffics = 0
    updated_traffics = 0
    skipped = 0

    # Build email→client_id index for collision detection
    c.execute("SELECT id, email, uuid FROM clients")
    email_to_id = {}
    for row in c.fetchall():
        email_to_id[row["email"]] = row["id"]

    # Read all inbounds and their settings JSON
    c.execute("SELECT id, settings FROM inbounds")
    inbounds = c.fetchall()

    for inbound in inbounds:
        inbound_id = inbound["id"]
        try:
            settings = json.loads(inbound["settings"])
        except json.JSONDecodeError:
            print(f"  WARN: inbound {inbound_id} has invalid settings JSON, skipping")
            continue

        json_clients = settings.get("clients", [])

        for jc in json_clients:
            uuid = jc.get("id", "")
            if not uuid:
                continue

            client_id = existing_clients.get(uuid)
            email = jc.get("email", "")

            if client_id is None:
                # Check if email already exists (UNIQUE constraint on email)
                existing_by_email = email_to_id.get(email)

                if existing_by_email is not None:
                    # Update existing client row with new UUID
                    sub_id = jc.get("subId", "")
                    flow = jc.get("flow", "")
                    limit_ip = jc.get("limitIp", 0)
                    total_gb = jc.get("totalGB", 0)
                    expiry_time = jc.get("expiryTime", 0)
                    enable = 1 if jc.get("enable", True) else 0

                    c.execute("""
                        UPDATE clients SET uuid = ?, sub_id = ?, flow = ?,
                                           limit_ip = ?, total_gb = ?, expiry_time = ?,
                                           enable = ?, updated_at = ?
                        WHERE id = ?
                    """, (uuid, sub_id, flow, limit_ip, total_gb,
                          expiry_time, enable, now_ms, existing_by_email))

                    client_id = existing_by_email
                    existing_clients[uuid] = client_id
                    updated_clients += 1
                    print(f"  ~ updated UUID for email={email} → {uuid[:8]}... (id={client_id})")
                else:
                    # Insert new client into clients table
                    sub_id = jc.get("subId", "")
                    flow = jc.get("flow", "")
                    limit_ip = jc.get("limitIp", 0)
                    total_gb = jc.get("totalGB", 0)
                    expiry_time = jc.get("expiryTime", 0)
                    enable = 1 if jc.get("enable", True) else 0

                    c.execute("""
                        INSERT INTO clients
                        (email, sub_id, uuid, password, auth, flow, security, reverse,
                         limit_ip, total_gb, expiry_time, enable, tg_id, group_name,
                         comment, reset, created_at, updated_at)
                        VALUES (?, ?, ?, '', '', ?, '', '',
                                ?, ?, ?, ?, 0, '', '', 0, ?, ?)
                    """, (email, sub_id, uuid, flow, limit_ip, total_gb,
                          expiry_time, enable, now_ms, now_ms))

                    client_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                    existing_clients[uuid] = client_id
                    email_to_id[email] = client_id
                    added_clients += 1
                    print(f"  + client UUID={uuid[:8]}... email={email} (id={client_id})")
            else:
                skipped += 1

            # Add client_inbounds mapping if missing. INSERT OR IGNORE: the
            # (client_id, inbound_id) pair is UNIQUE, and the in-memory set can
            # miss a pre-existing row when a client's uuid was re-bound in this
            # run (email-collision path) — ignore-if-exists is the correct
            # ensure-mapping semantic regardless. flow_override mirrors the
            # settings JSON client's flow (e.g. xtls-rprx-vision for Vision).
            if (client_id, inbound_id) not in existing_mappings:
                _flow_override = jc.get("flow", "")
                c.execute("""
                    INSERT OR IGNORE INTO client_inbounds (client_id, inbound_id, flow_override, created_at)
                    VALUES (?, ?, ?, ?)
                """, (client_id, inbound_id, _flow_override, now_ms))
                existing_mappings.add((client_id, inbound_id))
                added_mappings += 1
                print(f"  + mapping client_id={client_id} → inbound_id={inbound_id}")

            # Mirror enable + expiry into client_traffics (the config-gating table).
            if has_traffics:
                t_enable = 1 if jc.get("enable", True) else 0
                t_expiry = jc.get("expiryTime", 0)
                tkey = (email, inbound_id)
                ct = existing_traffics.get(tkey)
                if ct is None:
                    c.execute(
                        "INSERT INTO client_traffics "
                        "(inbound_id, enable, email, up, down, expiry_time, total, reset, last_online) "
                        "VALUES (?, ?, ?, 0, 0, ?, 0, 0, 0)",
                        (inbound_id, t_enable, email, t_expiry))
                    existing_traffics[tkey] = (
                        c.execute("SELECT last_insert_rowid()").fetchone()[0], t_enable, t_expiry)
                    added_traffics += 1
                    print(f"  + traffics email={email} enable={t_enable} expiry={t_expiry} (inbound {inbound_id})")
                elif ct[1] != t_enable or ct[2] != t_expiry:
                    c.execute("UPDATE client_traffics SET enable=?, expiry_time=? WHERE id=?",
                              (t_enable, t_expiry, ct[0]))
                    existing_traffics[tkey] = (ct[0], t_enable, t_expiry)
                    updated_traffics += 1
                    print(f"  ~ traffics email={email} enable={t_enable} expiry={t_expiry} (inbound {inbound_id})")

    db.commit()

    # Summary
    c.execute("SELECT count(*) FROM clients")
    total_clients = c.fetchone()[0]
    c.execute("SELECT count(*) FROM client_inbounds")
    total_mappings = c.fetchone()[0]

    print("\n=== Per-inbound client counts ===")
    for r in c.execute("SELECT inbound_id, count(*) as cnt FROM client_inbounds GROUP BY inbound_id"):
        print(f"  inbound {r[0]}: {r[1]} clients")

    print("\n=== SYNC COMPLETE ===")
    print(f"Added:   {added_clients} clients, {added_mappings} inbound mappings, {added_traffics} traffics")
    print(f"Updated: {updated_clients} clients, {updated_traffics} traffics (enable/expiry)")
    print(f"Skipped: {skipped} (already in clients table)")
    print(f"Total:   {total_clients} clients, {total_mappings} inbound mappings")

    db.close()
    return {"added_clients": added_clients, "updated_clients": updated_clients,
            "added_mappings": added_mappings, "added_traffics": added_traffics,
            "updated_traffics": updated_traffics, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mirror 3x-ui inbounds.settings clients into the v3 clients table."
    )
    parser.add_argument("--restart-if-changed", action="store_true",
                        help="Restart x-ui (regenerate xray config) only when the table changed")
    args = parser.parse_args(argv)

    stats = run_sync()
    changed = (stats["added_clients"] + stats["updated_clients"] + stats["added_mappings"]
               + stats.get("added_traffics", 0) + stats.get("updated_traffics", 0)) > 0
    print(f"\nSYNC_RESULT changed={1 if changed else 0} "
          f"added_clients={stats['added_clients']} "
          f"updated_clients={stats['updated_clients']} "
          f"added_mappings={stats['added_mappings']} "
          f"added_traffics={stats.get('added_traffics', 0)} "
          f"updated_traffics={stats.get('updated_traffics', 0)}")

    if changed and args.restart_if_changed:
        print("Table changed + --restart-if-changed: restarting x-ui so xray regenerates config...")
        _restart_xui()

    # Exit 10 (changed) only in the machine mode (--restart-if-changed); plain runs
    # always exit 0 so the existing manual sync workflow step does not fail on a
    # panel that has pending changes.
    return 10 if (changed and args.restart_if_changed) else 0


def _restart_xui() -> None:
    """Restart x-ui (so xray regenerates config.json from the clients table).
    Tries the panel CLI first, then the systemd unit (systemd services have a
    minimal PATH where `x-ui` may be absent)."""
    for cmd in (["x-ui", "restart"], ["systemctl", "restart", "x-ui"]):
        try:
            subprocess.run(cmd, check=False, timeout=60)
            print("restart issued via:", " ".join(cmd))
            return
        except FileNotFoundError:
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  WARN: {' '.join(cmd)} failed: {e}", file=sys.stderr)
            continue
    print("  WARN: could not restart x-ui (no x-ui / systemctl found).", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
