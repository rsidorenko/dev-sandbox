"""Find where 3x-ui stores creds + test login WITH CSRF."""
import sqlite3, os, subprocess, json, re

DB = "/etc/x-ui/x-ui.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()

print("=== ALL tables ===")
for (name,) in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    cur2 = conn.cursor()
    cur2.execute(f"SELECT count(*) FROM '{name}'")
    cnt = cur2.fetchone()[0]
    print(f"  {name} ({cnt} rows)")

print("\n=== users table schema + rows ===")
try:
    print(cur.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0])
    cols = [c[1] for c in cur.execute("PRAGMA table_info(users)")]
    for row in cur.execute(f"SELECT {','.join(cols)} FROM users"):
        d = dict(zip(cols, row))
        if 'password' in d: d['password'] = str(d['password'])[:6] + '...' if d['password'] else ''
        if 'password_hash' in d: d['password_hash'] = str(d['password_hash'])[:6] + '...' if d['password_hash'] else ''
        print(f"  {d}")
except Exception as e:
    print(f"  no users table: {e}")
conn.close()

PANEL_PASS = os.environ.get("LTE_PANEL_PASS", "")
print(f"\n=== Login test WITH CSRF (pass len={len(PANEL_PASS)}) ===")
import urllib.parse
# Fetch page to get CSRF
r = subprocess.run(["curl", "-sk", "https://localhost:54023/Cq6xxAccNLaSEBcR0L/", "-c", "/tmp/ck.txt"],
                   capture_output=True, text=True, timeout=15)
csrf = ""
m = re.search(r'csrf-token" content="([^"]+)"', r.stdout)
if m: csrf = m.group(1)
print(f"CSRF: {csrf[:16] if csrf else 'NONE'}...")
# Login with CSRF + cookie
r = subprocess.run(
    ["curl", "-sk", "-X", "POST", "https://localhost:54023/Cq6xxAccNLaSEBcR0L/login",
     "-H", "Content-Type: application/x-www-form-urlencoded",
     "-H", f"X-CSRF-Token: {csrf}",
     "-b", "/tmp/ck.txt", "-c", "/tmp/ck.txt",
     "-d", "username=bravada&password=" + urllib.parse.quote(PANEL_PASS),
     "-w", "\nHTTP_STATUS:%{http_code}"],
    capture_output=True, text=True, timeout=15)
print(r.stdout[:400])
