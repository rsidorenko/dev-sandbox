#!/usr/bin/env python3
"""Check vpn_servers table and inbound client resolution."""
import sqlite3, json

db = sqlite3.connect("/etc/x-ui/x-ui.db")
db.row_factory = sqlite3.Row
c = db.cursor()

# List all inbounds
print("=== All inbounds ===")
c.execute("SELECT id, port, protocol, settings FROM inbounds")
for r in c.fetchall():
    s = json.loads(r["settings"])
    clients = s.get("clients", [])
    emails = [cl.get("email", "") for cl in clients]
    print(f"  inbound {r['id']}: port={r['port']}, proto={r['protocol']}, clients={len(clients)}")
    # Show first 3 emails
    for e in emails[:3]:
        print(f"    email={e}")
    if len(emails) > 3:
        print(f"    ... and {len(emails)-3} more")

# Check client_traffics table
print("\n=== client_traffics count ===")
c.execute("SELECT count(*) FROM client_traffics")
print(f"  {c.fetchone()[0]} rows")

# Check if client_traffics has matching emails
print("\n=== client_traffics sample ===")
c.execute("SELECT email, enable FROM client_traffics LIMIT 5")
for r in c.fetchall():
    print(f"  email={r[0]}, enable={r[1]}")

db.close()
