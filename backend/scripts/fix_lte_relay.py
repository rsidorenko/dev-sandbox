"""Fix LTE relay outbound in 3x-ui SQLite database.

3x-ui stores xray template config (including outbounds) in its SQLite DB.
Editing config.json directly doesn't work - 3x-ui regenerates it from DB.

This script finds the xray template config in the DB and removes
xtls-rprx-vision flow from relay-to-frankfurt outbound.
"""

import json
import os
import subprocess
import sys

# 3x-ui DB locations
DB_CANDIDATES = [
    "/etc/x-ui/x-ui.db",
    "/usr/local/x-ui/bin/x-ui.db",
    "/opt/x-ui/x-ui.db",
]

db_path = None
for candidate in DB_CANDIDATES:
    if os.path.exists(candidate):
        db_path = candidate
        break

if not db_path:
    print("ERROR: 3x-ui DB not found in any candidate location")
    sys.exit(1)

print(f"Found 3x-ui DB: {db_path}")

# Check if sqlite3 is available
result = subprocess.run(["which", "sqlite3"], capture_output=True, text=True)
if result.returncode != 0:
    print("sqlite3 not available, trying python sqlite3")
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Find tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cursor.fetchall()]
    print(f"Tables: {tables}")

    # 3x-ui stores settings in 'settings' table (key-value)
    if "settings" in tables:
        cursor.execute("SELECT key, value FROM settings")
        for key, value in cursor.fetchall():
            if value and ("outbounds" in str(value) or "xrayTemplateConfig" in key.lower() or "xray" in key.lower()):
                print(f"\nFound key '{key}' (len={len(str(value))})")
                # Try to parse as JSON
                val_str = str(value)
                try:
                    cfg = json.loads(val_str)
                    if isinstance(cfg, dict) and "outbounds" in cfg:
                        print(f"  Has {len(cfg.get('outbounds', []))} outbounds")
                        fixed = False
                        for o in cfg.get("outbounds", []):
                            if o.get("tag") == "relay-to-frankfurt":
                                for v in o.get("settings", {}).get("vnext", []):
                                    for u in v.get("users", []):
                                        if u.get("flow") == "xtls-rprx-vision":
                                            u["flow"] = ""
                                            fixed = True
                                            print("  FIXED: removed flow")
                        if fixed:
                            new_val = json.dumps(cfg)
                            cursor.execute("UPDATE settings SET value=? WHERE key=?", (new_val, key))
                            conn.commit()
                            print(f"  DB updated for key '{key}'")
                except json.JSONDecodeError as e:
                    # Maybe it's a nested JSON string
                    print(f"  Not direct JSON: {e}")
    conn.close()
else:
    print("sqlite3 CLI available")
    # List settings
    result = subprocess.run(["sqlite3", db_path, "SELECT key FROM settings WHERE value LIKE '%relay-to-frankfurt%';"],
                          capture_output=True, text=True)
    print(f"Keys with relay: {result.stdout.strip()}")

print("\nDone. Now restart 3x-ui to regenerate config.json from fixed DB.")
