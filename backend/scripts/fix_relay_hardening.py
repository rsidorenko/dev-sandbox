"""Harden the RU relay (no Reality key changes -> user links unaffected). Two fixes
from the verification review:

1. ufw: replace the open `allow 1864/tcp` (0.0.0.0/0) with a source-restricted rule
   allowing only the production host, so the HTTP panel isn't reachable from the
   whole internet. (443 stays open to all for VPN users; 22 for admin.)

2. Reality `dest` camo: the inbound had dest=127.0.0.1:10443 (nothing listening),
   so active probes/scanners to :443 with SNI=max.ru got connection-refused -> a
   strong Reality-front fingerprint. Point dest at the real max.ru:443 so probes
   are forwarded to a genuine TLS 1.3 site serving a cert for max.ru (matches
   serverNames). Legit users are unaffected: Reality authenticates them before dest.

Runs on the relay via SSH (sudo). Idempotent.
"""

import json
import sqlite3
import subprocess
import time

PROD_IP = "109.120.178.49"  # production host the bot runs on
DEST_NEW = "max.ru:443"


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


# ── 1. ufw source-restriction on 1864 ──
print("=== ufw: source-restrict 1864 to prod ===")
# remove any open 1864 rule(s)
run("sudo ufw delete allow 1864/tcp 2>/dev/null; true")
run("sudo ufw delete allow from any to any port 1864 proto tcp 2>/dev/null; true")
# allow only prod
r = run(f"sudo ufw allow from {PROD_IP} to any port 1864 proto tcp")
print(f"  allow from {PROD_IP}: rc={r.returncode}")
print("  ufw 1864 rules now:", run("sudo ufw status | grep 1864").stdout.strip() or "(none)")

# ── 2. Reality dest camo ──
print("\n=== Reality dest -> max.ru:443 ===")
db = sqlite3.connect("/etc/x-ui/x-ui.db")
cur = db.cursor()
row = cur.execute("SELECT id, stream_settings FROM inbounds WHERE port=443 OR tag LIKE 'in-443%' LIMIT 1").fetchone()
if not row:
    print("ERROR: no 443 inbound found", flush=True)
else:
    ib_id, ss_raw = row
    ss = json.loads(ss_raw)
    rs = ss.get("realitySettings", {})
    old = rs.get("dest")
    rs["dest"] = DEST_NEW
    ss["realitySettings"] = rs
    # keep serverNames consistent with dest's cert (max.ru)
    rs["serverNames"] = ["max.ru"]
    cur.execute("UPDATE inbounds SET stream_settings=? WHERE id=?", (json.dumps(ss), ib_id))
    db.commit()
    print(f"  inbound id={ib_id} dest: {old} -> {DEST_NEW}; serverNames={rs['serverNames']}")
db.close()

# ── 3. restart + verify ──
print("\n=== restart x-ui + verify ===")
run("sudo systemctl restart x-ui")
time.sleep(8)
xray_ok = run("pgrep -f xray-linux-amd64").returncode == 0
port443 = run("sudo ss -tlnp | grep ':443 '").stdout.strip()
print(f"  xray running: {xray_ok}")
print(f"  :443 listening: {port443 or 'NO'}")
if not xray_ok or not port443:
    print("ERROR: xray failed after dest change; check journalctl -u x-ui", flush=True)
else:
    # confirm dest propagated to config.json
    cfg = run("sudo grep -o 'max.ru:443' /usr/local/x-ui/bin/config.json | head -1").stdout.strip()
    print(f"  config.json dest present: {cfg or 'NOT FOUND'}")
