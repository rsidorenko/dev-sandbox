"""Delete inbounds 1 and 6 from LTE 3x-ui SQLite DB via SSH.

The panel API delInbound returns 404 (old 3x-ui). Delete directly from
/etc/x-ui/x-ui.db and restart x-ui to regenerate config.json.

Run via SSH: scp this to LTE, ssh 'sudo python3 /tmp/script.py'
"""

import json
import os
import sqlite3
import subprocess
import sys
import time

DB_PATH = "/etc/x-ui/x-ui.db"
TARGET_INBOUNDS = [1, 6]


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: {DB_PATH} not found")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Show current inbounds
    cursor.execute("SELECT id, port, protocol, remark FROM inbounds")
    rows = cursor.fetchall()
    print(f"Current inbounds in DB: {[(r[0], r[1], r[2]) for r in rows]}")

    deleted = []
    for ib_id in TARGET_INBOUNDS:
        cursor.execute("SELECT id FROM inbounds WHERE id = ?", (ib_id,))
        if cursor.fetchone():
            cursor.execute("DELETE FROM inbounds WHERE id = ?", (ib_id,))
            print(f"Deleted inbound {ib_id} (rows affected: {cursor.rowcount})")
            deleted.append(ib_id)
        else:
            print(f"Inbound {ib_id} not found (already deleted?)")

    conn.commit()
    conn.close()
    print(f"\nDeleted from DB: {deleted}")

    if not deleted:
        print("Nothing to delete. Exiting without restart.")
        return

    # Restart x-ui to regenerate config.json from DB
    print("\nRestarting x-ui to apply...")
    result = subprocess.run(["sudo", "systemctl", "restart", "x-ui"],
                          capture_output=True, text=True, timeout=30)
    print(f"systemctl restart x-ui: rc={result.returncode}")
    time.sleep(5)

    # Verify
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, port, protocol FROM inbounds")
    rows = cursor.fetchall()
    conn.close()
    print(f"\nRemaining inbounds: {[(r[0], r[1], r[2]) for r in rows]}")

    # Check xray is running
    result = subprocess.run(["pgrep", "-f", "xray-linux-amd64"],
                          capture_output=True, text=True)
    if result.returncode == 0:
        print(f"xray running: PID(s) {result.stdout.strip()}")
    else:
        print("WARNING: xray not running after restart!")


if __name__ == "__main__":
    main()
