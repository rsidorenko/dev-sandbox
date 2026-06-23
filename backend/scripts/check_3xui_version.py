#!/usr/bin/env python3
"""Check 3x-ui installation details on a server."""
import sqlite3, json, subprocess, os

print("=== 3x-ui Version & Status ===")
r = subprocess.run(["x-ui", "version"], capture_output=True, text=True)
print(f"x-ui version: {r.stdout.strip()} {r.stderr.strip()}")

r = subprocess.run(["x-ui", "status"], capture_output=True, text=True)
print(f"x-ui status: {r.stdout.strip()} {r.stderr.strip()}")

# Check which 3x-ui fork (check binary/install script)
for p in ["/usr/local/x-ui/bin/x-ui-linux-amd64", "/usr/local/x-ui/x-ui"]:
    if os.path.exists(p):
        print(f"binary: {p} ({os.path.getsize(p)} bytes)")

# Check install source
if os.path.exists("/etc/x-ui/install.log"):
    print("\n=== Install log (last 10 lines) ===")
    with open("/etc/x-ui/install.log") as f:
        lines = f.readlines()
        for l in lines[-10:]:
            print(f"  {l.rstrip()}")

print("\n=== Database Info ===")
db = sqlite3.connect("/etc/x-ui/x-ui.db")
c = db.cursor()

# All settings
c.execute("SELECT key, value FROM settings")
for r in c.fetchall():
    val = r[1][:100] if r[1] else ""
    print(f"  {r[0]}: {val}")

# Inbounds summary
print("\n=== Inbounds ===")
c.execute("SELECT id, port, protocol, settings FROM inbounds")
for r in c.fetchall():
    s = json.loads(r[3])
    print(f"  inbound {r[0]}: port={r[1]}, proto={r[2]}, clients={len(s.get('clients',[]))}")

# Table list
print("\n=== Tables ===")
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
for r in c.fetchall():
    c2 = db.cursor()
    c2.execute(f"SELECT count(*) FROM \"{r[0]}\"")
    print(f"  {r[0]}: {c2.fetchone()[0]} rows")

# DB file sizes
for f in ["/etc/x-ui/x-ui.db", "/etc/x-ui/x-ui.db.bak"]:
    if os.path.exists(f):
        print(f"\n{f}: {os.path.getsize(f)} bytes")

db.close()

# Check OS/arch
print("\n=== OS ===")
r = subprocess.run(["uname", "-a"], capture_output=True, text=True)
print(r.stdout.strip())
