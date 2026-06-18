"""Register a relay UUID on THIS panel's :443 tcp+reality inbound (4 v3 stores).

Runs ON the foreign target panel (Frankfurt / Helsinki / LA) via SSH (sudo). A new
LTE entry chains its foreign traffic to this target as this relay client; the UUID
must exist on the target's inbound (clients + client_inbounds + settings JSON +
client_traffics) or xray rejects the relay ("invalid request user id"). Idempotent.

Env:
  RELAY_UUID   the relay UUID (from setup_lte.py's RELAY_UUID output)
  RELAY_EMAIL  distinct email per LTE entry on the target's inbound (default relay-from-lte)
  INBOUND_ID   the target's :443 tcp+reality inbound id (Frankfurt=1; verify Helsinki/LA)

Best practice: each LTE entry carries its OWN relay UUID (per-server identity, stats,
limits) — do NOT reuse another entry's UUID.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time

DB = "/etc/x-ui/x-ui.db"


def main() -> None:
    uuid = os.environ.get("RELAY_UUID", "").strip()
    email = os.environ.get("RELAY_EMAIL", "relay-from-lte").strip()
    inbound = int(os.environ.get("INBOUND_ID", "1"))
    if not uuid:
        print("ERROR: RELAY_UUID not set", file=sys.stderr)
        sys.exit(1)

    db = sqlite3.connect(DB)
    c = db.cursor()
    now = int(time.time())

    # 1. clients table (idempotent by uuid)
    row = c.execute("SELECT id FROM clients WHERE uuid=?", (uuid,)).fetchone()
    client_id = row[0] if row else c.execute(
        "INSERT INTO clients (email,uuid,enable,flow,limit_ip,total_gb,expiry_time,"
        "reset,created_at,updated_at) VALUES (?,?,?,?,0,0,0,0,?,?)",
        (email, uuid, 1, "", now, now)).lastrowid

    # 2. client_inbounds link (UNIQUE pair -> INSERT OR IGNORE)
    c.execute("INSERT OR IGNORE INTO client_inbounds (client_id,inbound_id) VALUES (?,?)",
              (client_id, inbound))

    # 3. inbound settings JSON clients array
    srow = c.execute("SELECT settings FROM inbounds WHERE id=?", (inbound,)).fetchone()
    settings = json.loads(srow[0]) if srow and srow[0] else {}
    clients = settings.setdefault("clients", [])
    if not any(cl.get("id") == uuid for cl in clients):
        clients.append({"id": uuid, "email": email, "enable": True, "flow": ""})
        c.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), inbound))

    # 4. client_traffics (gates config inclusion on v3; idempotent by (email,inbound))
    if not c.execute("SELECT id FROM client_traffics WHERE email=? AND inbound_id=?",
                     (email, inbound)).fetchone():
        c.execute("INSERT INTO client_traffics (inbound_id,enable,email,up,down,expiry_time,"
                  "total,reset,last_online) VALUES (?,1,?,0,0,0,0,0,0)", (inbound, email))

    db.commit()
    db.close()
    print(f"relay UUID {uuid[:8]}... registered on inbound {inbound} (4 stores) as {email!r}")

    # x-ui does not detect direct SQLite writes -> restart so xray regenerates config.json.
    for cmd in (["x-ui", "restart"], ["systemctl", "restart", "x-ui"]):
        try:
            subprocess.run(cmd, check=False, timeout=60)
            print("restart issued via:", " ".join(cmd))
            return
        except FileNotFoundError:
            continue
    print("WARN: could not restart x-ui (no x-ui / systemctl found).", file=sys.stderr)


if __name__ == "__main__":
    main()
