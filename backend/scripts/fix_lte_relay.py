"""Fix LTE relay outbound in 3x-ui SQLite database.

3x-ui stores xray template config in xrayTemplateConfig key in SQLite DB.
Editing config.json doesn't work - 3x-ui regenerates it from DB on restart.

This script updates xrayTemplateConfig in the DB to remove
xtls-rprx-vision flow from relay-to-frankfurt outbound.
"""

import json
import os
import sqlite3
import sys

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
    print("ERROR: 3x-ui DB not found")
    sys.exit(1)

print(f"DB: {db_path}")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Find xrayTemplateConfig setting
cursor.execute("SELECT key, value FROM settings WHERE key = 'xrayTemplateConfig'")
row = cursor.fetchone()

if not row:
    print("ERROR: xrayTemplateConfig not found in settings")
    # List all keys for debugging
    cursor.execute("SELECT key FROM settings")
    keys = [r[0] for r in cursor.fetchall()]
    print(f"Available keys: {keys}")
    conn.close()
    sys.exit(1)

key, value = row
print(f"Found key: {key} (len={len(value)})")

try:
    cfg = json.loads(value)
except json.JSONDecodeError as e:
    print(f"ERROR: xrayTemplateConfig is not valid JSON: {e}")
    conn.close()
    sys.exit(1)

outbounds = cfg.get("outbounds", [])
print(f"Outbounds in template: {len(outbounds)}")

fixed = False
for o in outbounds:
    if o.get("tag") == "relay-to-frankfurt":
        print(f"Found relay-to-frankfurt outbound")
        for v in o.get("settings", {}).get("vnext", []):
            print(f"  vnext: {v.get('address')}:{v.get('port')}")
            for u in v.get("users", []):
                flow = u.get("flow", "")
                print(f"    user flow: {flow!r}")
                if flow == "xtls-rprx-vision":
                    u["flow"] = ""
                    fixed = True
                    print(f"    -> FIXED: flow set to empty")

if fixed:
    new_value = json.dumps(cfg)
    cursor.execute(
        "UPDATE settings SET value = ? WHERE key = 'xrayTemplateConfig'",
        (new_value,)
    )
    conn.commit()
    print(f"DB updated! {len(new_value)} bytes written.")
else:
    print("No fix needed (flow already empty or relay not found)")

conn.close()
print("Done.")
