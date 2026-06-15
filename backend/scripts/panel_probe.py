"""Read-only probe: dump what xray ACTUALLY runs on a VPN panel (the generated
config.json), plus the x-ui settings that control logging.

Purpose: the manage_ru_egress `dump` shows the *template* (xrayTemplateConfig from
the settings table) and the *inbound sniffing* (DB rows), but NOT the generated
config.json that xray executes. When access/error logs are 0 bytes yet xray
warnings still reach journald, x-ui is regenerating config.json WITHOUT the
template's `log` section file paths — this probe confirms that and shows the live
routing + which inbound is ws/cdn (2.0) + every listener.

Run ON the panel as root. Pure reads, no writes, no restart.
"""

import json
import os
import sqlite3
import subprocess


def run(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


CONFIG = "/usr/local/x-ui/bin/config.json"

print("===== LIVE config.json — the config xray actually runs =====")
try:
    c = json.load(open(CONFIG))
    print("log section:", json.dumps(c.get("log"), ensure_ascii=False))
    routing = c.get("routing") or {}
    print("routing.domainStrategy:", routing.get("domainStrategy"))
    print("live routing rules:")
    for rule in routing.get("rules", []):
        print("  ", json.dumps(rule, ensure_ascii=False))
    print("live inbounds (network / path / security / sniffing):")
    for ib in c.get("inbounds", []):
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
    for o in c.get("outbounds", []):
        print("  ", json.dumps({k: o.get(k) for k in ("tag", "protocol")}, ensure_ascii=False))
except Exception as e:
    print(f"  could not read {CONFIG}: {e}")

print("\n===== ALL listening TCP ports (non-loopback) — is the ws inbound bound? =====")
print(run("ss -tlnp 2>/dev/null | grep -vE '127.0.0.1:|\\[::1\\]:'").strip() or "(none)")

print("\n===== nginx in front of xray? (ws/cdn sometimes terminates at nginx) =====")
print("nginx listening:", run("ss -tlnp 2>/dev/null | grep nginx").strip() or "(nginx not listening)")
nginx_conf = run("grep -rEn 'proxy_pass|upgrade|server_name|listen' "
                 "/etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | head -40")
print(nginx_conf.strip() or "(no nginx site/conf.d ws proxy found)")

print("\n===== x-ui settings — log / limit / stat / sub (access-log-free mode) =====")
for db in ("/etc/x-ui/x-ui.db", "/usr/local/x-ui/x-ui.db", "/usr/local/x-ui/bin/x-ui.db"):
    if os.path.exists(db):
        try:
            conn = sqlite3.connect(db)
            cur = conn.cursor()
            rows = cur.execute("SELECT key, value FROM settings").fetchall()
            for key, value in rows:
                k = (key or "").lower()
                if any(t in k for t in ("log", "limit", "stat", "sub", "access", "online")):
                    shown = (value[:400] + "…") if value and len(value) > 400 else value
                    print(f"  {key} = {shown}")
            conn.close()
        except Exception as e:
            print(f"  settings read error: {e}")
        break

print("\n===== access/error log files + xray =====")
print(run("ls -la /var/log/xray-*.log /usr/local/x-ui/*.log 2>/dev/null").strip() or "(none)")
print("xray:", run("ps -ef | grep '[x]ray'").strip() or "(not running)")
