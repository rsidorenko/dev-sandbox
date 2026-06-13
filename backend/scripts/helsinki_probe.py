"""Check whether Helsinki's v3 clients table recognizes the RU relay UUID.

The RU relay's relay-to-helsinki outbound dials Helsinki:443 with RELAY_UUID. On
3x-ui v3.x, xray reads its client list from the clients/client_inbounds tables,
NOT inbounds.settings JSON. This prints whether RELAY_UUID is in Helsinki's
clients table (the thing that actually matters) vs only in settings JSON.

Runs on Helsinki (scp + run via sshpass from prod).
"""
import json
import sqlite3

RU = "00607f0b-a9e7-4280-abb3-2231e1b9c2ff"
db = sqlite3.connect("/etc/x-ui/x-ui.db")

in_clients = db.execute("SELECT id,email,enable FROM clients WHERE uuid=?", (RU,)).fetchall()
print("relay UUID in clients table:", in_clients if in_clients else "NOT FOUND")

# which inbound(s) is it linked to?
if in_clients:
    cid = in_clients[0][0]
    links = db.execute("SELECT inbound_id FROM client_inbounds WHERE client_id=?", (cid,)).fetchall()
    print("linked to inbound_id(s):", [r[0] for r in links])

print("client_inbounds total (inbound 1):",
      db.execute("SELECT count(*) FROM client_inbounds WHERE inbound_id=1").fetchone()[0])

try:
    s = json.loads(db.execute("SELECT settings FROM inbounds WHERE id=1").fetchone()[0])
    uuids = [c.get("id") for c in s.get("clients", [])]
    print(f"settings.clients inbound 1 count: {len(uuids)} | relay UUID in settings JSON: {RU in uuids}")
except Exception as e:
    print("settings read error:", e)

db.close()
