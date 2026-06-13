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

section("3x-ui inbounds DB rows")
db = next((p for p in DB_CANDIDATES if os.path.exists(p)), None)
if db:
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT id, port, protocol, tag, enable, remark FROM inbounds")
        for row in cur.fetchall():
            print(f"  {row}")
        # dump the 443 inbound's streamSettings for inspection
        cur.execute("SELECT stream_settings FROM inbounds WHERE port=443 OR tag LIKE 'in-443%'")
        for (ss,) in cur.fetchall():
            print(f"  443 stream_settings: {ss}")
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

section("Helsinki reachability")
r = run("timeout 4 bash -c 'echo > /dev/tcp/77.221.159.106/443' 2>/dev/null && echo REACHABLE || echo UNREACHABLE")
print(r.stdout)

section("geo files")
print(run("ls -la /usr/local/x-ui/bin/*.dat 2>/dev/null").stdout or "(none)")
