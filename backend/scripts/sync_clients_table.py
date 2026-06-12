#!/usr/bin/env python3
"""Sync inbounds.settings clients → clients + client_inbounds tables.

3x-ui v3.x uses a separate clients table for xray config generation.
The bot's API (/panel/api/inbounds/update) writes only to inbounds.settings JSON.
This script mirrors JSON clients into the database tables so xray picks them up.

Run on the VPN server itself: python3 sync_clients_table.py
"""
import sqlite3, json, time, sys

DB_PATH = "/etc/x-ui/x-ui.db"

def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    c = db.cursor()

    # Check if clients table exists (v3.x only)
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='clients'")
    if not c.fetchone():
        print("SKIP: No 'clients' table (3x-ui v2.x — uses inbounds.settings directly)")
        db.close()
        return

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

    added_clients = 0
    updated_clients = 0
    added_mappings = 0
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

            # Add client_inbounds mapping if missing
            if (client_id, inbound_id) not in existing_mappings:
                c.execute("""
                    INSERT INTO client_inbounds (client_id, inbound_id, flow_override, created_at)
                    VALUES (?, ?, '', ?)
                """, (client_id, inbound_id, now_ms))
                existing_mappings.add((client_id, inbound_id))
                added_mappings += 1
                print(f"  + mapping client_id={client_id} → inbound_id={inbound_id}")

    db.commit()

    # Summary
    c.execute("SELECT count(*) FROM clients")
    total_clients = c.fetchone()[0]
    c.execute("SELECT count(*) FROM client_inbounds")
    total_mappings = c.fetchone()[0]

    # List inbound counts
    print("\n=== Per-inbound client counts ===")
    for r in c.execute("SELECT inbound_id, count(*) as cnt FROM client_inbounds GROUP BY inbound_id"):
        print(f"  inbound {r[0]}: {r[1]} clients")

    print(f"\n=== SYNC COMPLETE ===")
    print(f"Added:   {added_clients} clients, {added_mappings} inbound mappings")
    print(f"Updated: {updated_clients} (email collision, UUID replaced)")
    print(f"Skipped: {skipped} (already in clients table)")
    print(f"Total:   {total_clients} clients, {total_mappings} inbound mappings")

    db.close()

if __name__ == "__main__":
    main()
