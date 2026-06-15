"""Read-only probe: dump what xray ACTUALLY runs on a VPN panel (the generated
config.json), the LIVE access log at the path xray really uses, and the x-ui
settings that control logging.

Purpose: manage_ru_egress `dump`/`reset-logs` check `/var/log/xray-access.log`,
but x-ui OVERRIDES the template's log.access path to `/var/log/x-ui/xray-access.log`
— so those checks see an empty decoy file while the real access log (with the
[inbound >> outbound] routing decisions) sits unread elsewhere. This probe reads
the real path from config.json and surfaces the ws (2.0) inbound's routing.

Run ON the panel as root. Pure reads, no writes, no restart.
"""

import json
import os
import sqlite3
import subprocess


def run(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


CONFIG = "/usr/local/x-ui/bin/config.json"
cfg = None

print("===== LIVE config.json — the config xray actually runs =====")
try:
    cfg = json.load(open(CONFIG))
    print("log section:", json.dumps(cfg.get("log"), ensure_ascii=False))
    routing = cfg.get("routing") or {}
    print("routing.domainStrategy:", routing.get("domainStrategy"))
    print("live routing rules:")
    for rule in routing.get("rules", []):
        print("  ", json.dumps(rule, ensure_ascii=False))
    print("live inbounds (network / path / security / sniffing):")
    for ib in cfg.get("inbounds", []):
        ss = ib.get("streamSettings", {}) or {}
        ws = ss.get("wsSettings") or {}
        xh = ss.get("xhttpSettings") or {}
        print(f"  port={ib.get('port')} tag={ib.get('tag')} proto={ib.get('protocol')} "
              f"listen={ib.get('listen')!r} network={ss.get('network')!r} "
              f"security={ss.get('security')!r}")
        if ws:
            print(f"    ws path={ws.get('path')!r} host={ws.get('host')!r} "
                  f"headers={ws.get('headers')}")
        if xh:
            print(f"    xhttp path={xh.get('path')!r} host={xh.get('host')!r} "
                  f"mode={xh.get('mode')!r}")
        print(f"    sniffing={ib.get('sniffing')}")
    print("live outbounds:")
    for o in cfg.get("outbounds", []):
        print("  ", json.dumps({k: o.get(k) for k in ("tag", "protocol")}, ensure_ascii=False))
except Exception as e:
    print(f"  could not read {CONFIG}: {e}")

# The access log: x-ui OVERRIDES the template's log.access path. Read the REAL path
# from config.json (not the /var/log/xray-access.log decoy manage_ru_egress checks).
access_path = ((cfg or {}).get("log") or {}).get("access")
print("\n===== ACCESS LOG (real path from config.json) — routing decisions =====")
print(f"path: {access_path}")
if access_path:
    print(run(f"ls -la {access_path} 2>/dev/null").strip() or "(file not found)")
    print("--- log time range ---")
    print(run(f"head -1 {access_path} 2>/dev/null | cut -d' ' -f1-2; echo '  ...'; "
              f"tail -1 {access_path} 2>/dev/null | cut -d' ' -f1-2").strip())
    print("--- inbound tag breakdown (WHOLE access log) — does ws (2.0) EVER reach xray? ---")
    print(run(f"grep -oE 'in-[0-9]+-tcp' {access_path} 2>/dev/null | sort | uniq -c").strip()
          or "(no inbound-tagged entries at all)")
    print("--- ANY ws inbound (in-80-tcp) entries in whole log (last 25) ---")
    print(run(f"grep 'in-80-tcp' {access_path} 2>/dev/null | tail -25").strip()
          or "(ZERO ws inbound entries in entire log -> 2.0 not reaching this xray's routing)")
    print("--- RU-destination traffic on ws (in-80-tcp) — does 2.0 RU hit ru-relay? ---")
    print(run(f"grep 'in-80-tcp' {access_path} 2>/dev/null | grep -E '\\.ru|\\.su' | tail -25").strip()
          or "(no ws+RU entries)")
    print("--- recent RU traffic (any inbound) → outbound (last 25) ---")
    print(run(f"tail -8000 {access_path} 2>/dev/null | grep -E '\\.ru|\\.su' | tail -25").strip()
          or "(no .ru/.su traffic in log)")

print("\n===== ALL listening TCP ports (non-loopback) — is the ws inbound bound? =====")
print(run("ss -tlnp 2>/dev/null | grep -vE '127.0.0.1:|\\[::1\\]:'").strip() or "(none)")

print("\n===== x-ui settings — log / limit / stat / sub (access-log-free mode) =====")
for db in ("/etc/x-ui/x-ui.db", "/usr/local/x-ui/x-ui.db", "/usr/local/x-ui/bin/x-ui.db"):
    if os.path.exists(db):
        try:
            conn = sqlite3.connect(db)
            cur = conn.cursor()
            for key, value in cur.execute("SELECT key, value FROM settings").fetchall():
                k = (key or "").lower()
                if any(t in k for t in ("log", "limit", "stat", "sub", "access", "online")):
                    shown = (value[:300] + "…") if value and len(value) > 300 else value
                    print(f"  {key} = {shown}")
            conn.close()
        except Exception as e:
            print(f"  settings read error: {e}")
        break

print("\n===== xray =====")
print(run("ps -ef | grep '[x]ray'").strip() or "(not running)")
