"""Check ALL 3x-ui settings + CLI setting syntax + test login."""
import sqlite3, os, subprocess, json

DB = "/etc/x-ui/x-ui.db"

print("=== ALL settings keys+values ===")
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("SELECT key, value FROM settings ORDER BY key")
for k, v in cur.fetchall():
    vstr = str(v)
    if len(vstr) > 60:
        vstr = vstr[:60] + f"...(len={len(str(v))})"
    print(f"  {k} = {vstr}")
conn.close()

print("\n=== x-ui setting subcommand help ===")
r = subprocess.run(["x-ui", "setting", "-h"], capture_output=True, text=True, timeout=10)
print(r.stdout[:600] or r.stderr[:600])

print("\n=== x-ui top-level help (full) ===")
r = subprocess.run(["x-ui"], capture_output=True, text=True, timeout=10)
print(r.stdout[:800] or r.stderr[:800])

PANEL_PASS = os.environ.get("LTE_PANEL_PASS", "")
print(f"\n=== Test login https bravada (pass len={len(PANEL_PASS)}) ===")
import urllib.parse
r = subprocess.run(
    ["curl", "-sk", "-X", "POST", "https://localhost:54023/Cq6xxAccNLaSEBcR0L/login",
     "-H", "Content-Type: application/x-www-form-urlencoded",
     "-d", "username=bravada&password=" + urllib.parse.quote(PANEL_PASS),
     "-w", "\nHTTP_STATUS:%{http_code}"],
    capture_output=True, text=True, timeout=15)
print(r.stdout[:400])
