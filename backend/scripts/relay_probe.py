"""Deep diagnostic for the RU relay server. Runs ON the server (sudo).

Prints: xray process + cmdline, :443 listener, config.json inbounds, the 3x-ui
inbounds DB rows, journalctl of x-ui (where xray's startup/bind errors land),
geo files, and Helsinki reachability. Read-only.

Used when :443 is not listening despite xray running — surfaces why the inbound
failed to bind (bad Reality key, malformed settings, schema gap, etc.).
"""

import json
import os
import sqlite3
import subprocess
import sys

DB_CANDIDATES = [
    "/etc/x-ui/x-ui.db",
    "/usr/local/x-ui/x-ui.db",
    "/usr/local/x-ui/bin/x-ui.db",
    "/opt/x-ui/x-ui.db",
]
CONFIG = "/usr/local/x-ui/bin/config.json"


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def section(title):
    print(f"\n===== {title} =====")


print("===== xray process + cmdline =====")
print(run("ps -ef | grep '[x]ray'").stdout or "(no xray process)")
print(run("systemctl is-active x-ui xray 2>/dev/null || true").stdout)

section(":443 listener")
print(run("ss -tlnp 2>/dev/null | grep ':443 '").stdout or "(nothing on :443)")

section("config.json inbounds")
try:
    c = json.load(open(CONFIG))
    for ib in c.get("inbounds", []):
        ss = ib.get("streamSettings", {}) or {}
        print(f"  tag={ib.get('tag')} port={ib.get('port')} proto={ib.get('protocol')} "
              f"listen={ib.get('listen')!r} security={ss.get('security')} network={ss.get('network')}")
    print(f"  routing domainStrategy={c.get('routing', {}).get('domainStrategy')}")
    print(f"  outbounds={[o.get('tag') for o in c.get('outbounds', [])]}")
except Exception as e:
    print(f"  could not read {CONFIG}: {e}")

section("3x-ui inbounds DB rows + 443 clients")
db = next((p for p in DB_CANDIDATES if os.path.exists(p)), None)
if db:
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT id, port, protocol, tag, enable, remark FROM inbounds")
        rows = cur.fetchall()
        for row in rows:
            print(f"  inbound: {row}")
        ib443 = [r for r in rows if r[1] == 443 or (r[3] or "").startswith("in-443")]
        ib_id = ib443[0][0] if ib443 else None
        # streamSettings (Reality config)
        if ib_id:
            cur.execute("SELECT stream_settings FROM inbounds WHERE id=?", (ib_id,))
            for (ss,) in cur.fetchall():
                print(f"  443 stream_settings: {ss}")
            # clients in settings JSON (older)
            cur.execute("SELECT settings FROM inbounds WHERE id=?", (ib_id,))
            srow = cur.fetchone()
            try:
                sjson = json.loads(srow[0]) if srow else {}
                print(f"  443 settings.clients count: {len(sjson.get('clients', []))}")
            except Exception:
                print("  443 settings: (unreadable)")
            # v3 clients table
            try:
                cur.execute("SELECT count(*) FROM client_inbounds WHERE inbound_id=?", (ib_id,))
                print(f"  443 client_inbounds count (v3 table): {cur.fetchone()[0]}")
                cur.execute("SELECT c.email, substr(c.uuid,1,8), c.enable FROM clients c "
                            "JOIN client_inbounds ci ON ci.client_id=c.id WHERE ci.inbound_id=? LIMIT 8", (ib_id,))
                for cr in cur.fetchall():
                    print(f"    client: email={cr[0]} uuid={cr[1]}.. enable={cr[2]}")
            except Exception as e:
                print(f"  (no client_inbounds table: {e})")
            # Per-client traffic stats — decisive for whether the relay is actually
            # receiving connections (online-stats API disables the access log, so
            # traffic counters are the reliable signal). up/down > 0 => the relay
            # UUID is being used (foreign servers are relaying through here).
            try:
                cur.execute("SELECT * FROM client_traffics LIMIT 20")
                cols = [d[0] for d in cur.description]
                print(f"  client_traffics cols: {cols}")
                for r in cur.fetchall():
                    print(f"    {r}")
            except Exception as e:
                print(f"  (client_traffics: {e})")
        conn.close()
    except Exception as e:
        print(f"  DB read error: {e}")
else:
    print("  no x-ui.db found")

section("journalctl x-ui (last 30)")
print(run("journalctl -u x-ui --no-pager -n 30 2>/dev/null").stdout or "(no x-ui journal)")

section("xray logs (last 15) — common paths")
for p in ["/var/log/xray-error.log", "/var/log/xray-access.log",
          "/usr/local/x-ui/access.log", "/usr/local/x-ui/error.log"]:
    r = run(f"sudo tail -15 {p} 2>/dev/null")
    if r.stdout.strip():
        print(f"--- {p} ---")
        print(r.stdout)

section("panel local reachability + host firewall")
print("local curl :1864:", run("curl -sk --max-time 5 https://localhost:1864/ -o /dev/null -w '%{http_code}' 2>&1").stdout)
print(":1864 listening:", run("ss -tlnp 2>/dev/null | grep ':1864 '").stdout or "(not listening)")
print("iptables INPUT:", run("sudo iptables -S INPUT 2>/dev/null | head -20").stdout or "(no iptables)")
print("iptables (panel/443 rules):", run("sudo iptables -S 2>/dev/null | grep -iE '1864|443|dport|ACCEPT|DROP' | head -20").stdout)
print("ufw:", run("sudo ufw status 2>/dev/null || echo 'no ufw'").stdout)

section("Helsinki reachability")
r = run("timeout 4 bash -c 'echo > /dev/tcp/77.221.159.106/443' 2>/dev/null && echo REACHABLE || echo UNREACHABLE")
print(r.stdout)

section("geo files")
print(run("ls -la /usr/local/x-ui/bin/*.dat 2>/dev/null").stdout or "(none)")
