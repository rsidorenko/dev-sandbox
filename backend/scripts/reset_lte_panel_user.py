"""Reset LTE panel creds directly in the users table (bcrypt hash).

The `x-ui setting` CLI didn't work on this 3x-ui version (showed interactive
menu instead). Edit the users table directly: set username='bravada' and
password=bcrypt(LTE_PANEL_PASS). Then restart x-ui.
"""
import sqlite3, os, subprocess, sys

DB = "/etc/x-ui/x-ui.db"
NEW_USER = "bravada"
PASS = os.environ.get("LTE_PANEL_PASS", "")

if not PASS:
    print("ERROR: LTE_PANEL_PASS not set", file=sys.stderr)
    sys.exit(1)

# Generate bcrypt hash
def bcrypt_hash(pw):
    try:
        import bcrypt
        return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()
    except ImportError:
        pass
    # fallback: htpasswd
    r = subprocess.run(["htpasswd", "-bnBC", "10", "", pw], capture_output=True, text=True)
    if r.returncode == 0:
        return r.stdout.split(":", 1)[1].strip()
    # fallback: install bcrypt
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "bcrypt"],
                       capture_output=True, text=True, timeout=60)
    import bcrypt
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()

print("Generating bcrypt hash...")
pw_hash = bcrypt_hash(PASS)
print(f"hash: {pw_hash[:10]}... (len={len(pw_hash)})")

print("\nUpdating users table...")
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("SELECT id, username FROM users")
rows = cur.fetchall()
print(f"current users: {rows}")
if rows:
    # Update first user
    uid = rows[0][0]
    cur.execute("UPDATE users SET username=?, password=?, login_epoch=0 WHERE id=?",
                (NEW_USER, pw_hash, uid))
    print(f"updated user id={uid} -> username={NEW_USER}")
else:
    cur.execute("INSERT INTO users (username, password, login_epoch) VALUES (?, ?, 0)",
                (NEW_USER, pw_hash))
    print(f"inserted user username={NEW_USER}")
conn.commit()
conn.close()

print("\nRestarting x-ui...")
r = subprocess.run(["systemctl", "restart", "x-ui"], capture_output=True, text=True)
print(f"restart rc={r.returncode}")
import time
time.sleep(6)

# Test login
import urllib.parse, re
r = subprocess.run(["curl", "-sk", "https://localhost:54023/Cq6xxAccNLaSEBcR0L/", "-c", "/tmp/ck2.txt"],
                   capture_output=True, text=True, timeout=15)
csrf = ""
m = re.search(r'csrf-token" content="([^"]+)"', r.stdout)
if m:
    csrf = m.group(1)
r = subprocess.run(
    ["curl", "-sk", "-X", "POST", "https://localhost:54023/Cq6xxAccNLaSEBcR0L/login",
     "-H", "Content-Type: application/x-www-form-urlencoded",
     "-H", f"X-CSRF-Token: {csrf}",
     "-b", "/tmp/ck2.txt", "-c", "/tmp/ck2.txt",
     "-d", f"username={NEW_USER}&password=" + urllib.parse.quote(PASS),
     "-w", "\nHTTP_STATUS:%{http_code}"],
    capture_output=True, text=True, timeout=15)
print(f"login test: {r.stdout[:200]}")
