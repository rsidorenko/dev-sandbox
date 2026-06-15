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
import re
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
    # Derive the ws (2.0) inbound tag(s) from config.json so this works on any panel
    # (Frankfurt: in-80-tcp port 80; Helsinki: inbound-8080 port 8080).
    ws_tags = [ib.get("tag") for ib in (cfg or {}).get("inbounds", [])
               if ((ib.get("streamSettings") or {}).get("network") == "ws") and ib.get("tag")]
    print(f"ws (2.0) inbound tags from config.json: {ws_tags}")
    for tag in ws_tags:
        tag_escaped = re.escape(str(tag))
        print(f"--- {tag} (ws/2.0) outbound breakdown (whole log) ---")
        print(run(f"grep -oE '\\[{tag_escaped} -> [a-z-]+\\]' {access_path} 2>/dev/null "
                  f"| sort | uniq -c").strip() or "(no entries for this ws inbound)")
        print(f"--- {tag} (ws/2.0) RU traffic going -> direct (routing MISS, if any) ---")
        print(run(f"grep '{tag} -> direct' {access_path} 2>/dev/null | grep -E '\\.ru|\\.su|' "
                  f"| tail -20").strip() or "(none — no ws RU traffic goes direct ✓)")
        print(f"--- {tag} (ws/2.0) recent RU -> ru-relay (last 15, proof it works) ---")
        print(run(f"grep '{tag} -> ru-relay' {access_path} 2>/dev/null | grep -E '\\.ru|\\.su' "
                  f"| tail -15").strip() or "(no ws→ru-relay entries)")
    print("--- recent RU traffic (ANY inbound) → outbound (last 20) ---")
    print(run(f"tail -8000 {access_path} 2>/dev/null | grep -E '\\.ru|\\.su' | tail -20").strip()
          or "(no .ru/.su traffic in log)")
    # The 2.0 (ws) smoking gun: bare-IP destinations going >> direct because sniffing
    # can't recover a domain on ws (so geoip:ru is the only matcher, and it misses).
    print("--- bare-IP destinations on ws (2.0) -> direct (the Ozon breakage) ---")
    for tag in ws_tags:
        tag_escaped = re.escape(str(tag))
        print(f"  {tag} -> direct, top destinations:")
        print(run(f"grep -E '\\[{tag_escaped} (>>|->) direct\\]' {access_path} 2>/dev/null "
                  f"| grep -oE 'tcp:[^ ]+:443|tcp:[^ ]+:80' | sort | uniq -c | sort -rn | head -15").strip()
              or "(none)")
    # Cross-inbound check: does the same bare IP route to ru-relay on tcp/xhttp (1.0/3.0)?
    # If yes -> sniffing recovers the domain there but not on ws. The decisive comparison.
    print("--- bare IPs that went -> direct on ws: cross-inbound routing (whole log) ---")
    tag_alt = "|".join(re.escape(str(t)) for t in ws_tags)
    suspicious = run(f"grep -E '\\[({tag_alt}) (>>|->) direct\\]' "
                     f"{access_path} 2>/dev/null | grep -oE 'tcp:[0-9.]+:|tcp:\\[[0-9a-f:]+\\]:' "
                     f"| sort -u | head -8").splitlines()
    for line in suspicious:
        ip = line.replace("tcp:", "").rstrip(":").strip("[]")
        if not ip:
            continue
        print(f"  {ip}:")
        cross = run(f"grep '{ip}' {access_path} 2>/dev/null | grep -oE '\\[[^]]+\\]' | sort | uniq -c").strip()
        print("    " + (cross.replace("\n", "\n    ") or "(never appears elsewhere)"))
        print("    rdns: " + run(f"host {ip} 2>/dev/null | head -1").strip())
        print("    geo: " + run(f"curl -s --max-time 5 'http://ip-api.com/line/{ip}?fields=countryCode,org,as' 2>/dev/null").strip().replace("\n", " / "))
    print("--- OZON traffic (ANY inbound, whole log) — where does it route? ---")
    print(run(f"grep -iE 'ozon' {access_path} 2>/dev/null | tail -30").strip()
          or "(NO ozon entries anywhere -> Ozon never reached this xray)")
    for tag in ws_tags:
        print(f"--- LAST 40 entries on {tag} (ws/2.0, any dest) — catch the fresh test ---")
        print(run(f"grep '{tag}' {access_path} 2>/dev/null | tail -40").strip()
              or "(no entries)")

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
