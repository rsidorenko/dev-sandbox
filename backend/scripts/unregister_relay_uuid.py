"""Remove a relay UUID from THIS panel's inbound (4 v3 stores). Inverse of the
register pattern. Runs ON the target panel via SSH (sudo). Idempotent.

Env:
  RELAY_UUID   the relay UUID to remove
  RELAY_EMAIL  the relay client email (to clear client_traffics; optional — derived
               from the clients table if not set)
  INBOUND_ID   target inbound id (default 1)
"""

import json
import os
import sqlite3
import subprocess
import sys

DB = "/etc/x-ui/x-ui.db"


def main() -> None:
    uuid = os.environ.get("RELAY_UUID", "").strip()
    email = os.environ.get("RELAY_EMAIL", "").strip()
    inbound = int(os.environ.get("INBOUND_ID", "1"))
    if not uuid:
        print("ERROR: RELAY_UUID not set", file=sys.stderr)
        sys.exit(1)

    db = sqlite3.connect(DB)
    c = db.cursor()

    row = c.execute("SELECT id, email FROM clients WHERE uuid=?", (uuid,)).fetchone()
    if row:
        cid, c_email = row
        email = email or c_email
        c.execute("DELETE FROM client_inbounds WHERE client_id=?", (cid,))
        c.execute("DELETE FROM clients WHERE id=?", (cid,))
        print(f"removed clients/client_inbounds rows for id={cid} (email={email})")
    else:
        print("UUID not in clients table (already removed?)")

    if email:
        ct = c.execute("DELETE FROM client_traffics WHERE email=?", (email,)).rowcount
        if ct:
            print(f"removed {ct} client_traffics row(s) for email={email}")

    srow = c.execute("SELECT settings FROM inbounds WHERE id=?", (inbound,)).fetchone()
    if srow and srow[0]:
        settings = json.loads(srow[0])
        clients = settings.get("clients", [])
        before = len(clients)
        settings["clients"] = [cl for cl in clients if cl.get("id") != uuid]
        if len(settings["clients"]) != before:
            c.execute("UPDATE inbounds SET settings=? WHERE id=?",
                      (json.dumps(settings), inbound))
            print(f"removed UUID from settings JSON ({before} -> {len(settings['clients'])})")

    db.commit()
    db.close()
    print(f"relay UUID {uuid[:8]}... unregistered on inbound {inbound}")

    for cmd in (["x-ui", "restart"], ["systemctl", "restart", "x-ui"]):
        try:
            subprocess.run(cmd, check=False, timeout=60)
            print("restart issued via:", " ".join(cmd))
            return
        except FileNotFoundError:
            continue


if __name__ == "__main__":
    main()
