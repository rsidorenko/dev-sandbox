"""Check LTE panel stored creds in 3x-ui DB + test login."""
import sqlite3, os, subprocess, sys, json, re

DB = "/etc/x-ui/x-ui.db"

print("=== Panel creds stored in 3x-ui DB ===")
conn = sqlite3.connect(DB)
cur = conn.cursor()
for key in ("webUsername", "webPassword", "webPort", "webBasePath", "webCertFile", "webKeyFile", "sessionMaxAge"):
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    val = row[0] if row else "(not set)"
    if key == "webPassword" and val and val != "(not set)":
        val = val[:4] + "..." + f" (len={len(val)})"
    print(f"  {key} = {val}")
conn.close()

print("\n=== Test login via curl from localhost ===")
r = subprocess.run(
    ["curl", "-sk", "-X", "POST", "http://localhost:54023/Cq6xxAccNLaSEBcR0L/login",
     "-H", "Content-Type: application/x-www-form-urlencoded",
     "-d", "username=bravada&password=" + os.environ.get("LTE_PANEL_PASS", ""),
     "-w", "\nHTTP_STATUS:%{http_code}"],
    capture_output=True, text=True, timeout=15)
print(r.stdout[:500])
print("stderr:", r.stderr[:200] if r.stderr else "(none)")
