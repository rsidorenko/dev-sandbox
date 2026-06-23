#!/usr/bin/env python3
"""Read-only probe of the 1.0 (tcp/reality, :443) inbound health on a 3x-ui panel.

Run ON the panel. Pure reads, no writes, no restart. Answers: is xray up, is :443
listening, is the firewall open, is the reality dest reachable, and how many clients
does the 1.0 inbound serve LIVE (config.json) vs configured-but-disabled (x-ui.db).
"""
import json, os, sqlite3, subprocess, time

def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()

def read_file(path):
    try:
        with open(path) as f:
            return f.read()
    except PermissionError:
        return sh(f"sudo -n cat '{path}' 2>/dev/null")
    except FileNotFoundError:
        return None

print("===== HOST =====", sh("hostname"), sh("hostname -I"))

print("\n===== xray process =====")
ps = sh("ps -ef | grep '[x]ray'")
print(ps or "(NOT RUNNING)")
xui = sh("systemctl is-active x-ui 2>/dev/null")
print("x-ui service:", xui or "(n/a)")

print("\n===== listening TCP (public) =====")
print(sh("sudo -n ss -tlnp 2>/dev/null | grep -vE '127.0.0.1|\\[::1\\]' | grep -E ':443|:8443|:8080|:80 |:2053'") or "(nothing on :443/:8443/.8080/.80/.2053)")

print("\n===== firewall =====")
print(sh("sudo -n ufw status 2>/dev/null | grep -E '443|8080|8443|Status' | head -20") or "(ufw n/a or no rules shown)")

# config.json — what xray actually runs
cfg = None
for cp in ("/usr/local/x-ui/bin/config.json",):
    t = read_file(cp)
    if t:
        try:
            cfg = json.loads(t)
        except Exception as e:
            print("config parse err:", e)
        break

print("\n===== LIVE config.json inbounds =====")
live_10_clients = None
if cfg:
    for ib in cfg.get("inbounds", []):
        ss = ib.get("streamSettings", {}) or {}
        net = ss.get("network")
        sec = ss.get("security")
        clients = (ib.get("settings", {}) or {}).get("clients") or []
        print(f"  port={ib.get('port')} tag={ib.get('tag')} proto={ib.get('protocol')} "
              f"network={net!r} security={sec!r} listen={ib.get('listen')!r} live_clients={len(clients)}")
        if sec == "reality" and net in ("tcp", None):
            rs = ss.get("realitySettings", {}) or {}
            dest = rs.get("dest")
            print(f"    REALITY dest={dest!r} serverNames={rs.get('serverNames')} "
                  f"shortIds={rs.get('shortIds')} privateKey_present={bool(rs.get('privateKey'))}")
            live_10_clients = clients
            # test dest reachability (classic "странно не пингуется" cause)
            if dest and ":" in dest:
                dh, dp = dest.rsplit(":", 1)
                ok = sh(f"timeout 5 bash -c '</dev/tcp/{dh}/{dp}' 2>/dev/null && echo REACHABLE || echo UNREACHABLE")
                print(f"    dest {dest} TCP connect: {ok}")
else:
    print("  (no config.json)")

if live_10_clients is not None:
    now_ms = int(time.time() * 1000)
    disabled = [c for c in live_10_clients if c.get("enable") is False]
    expired = [c for c in live_10_clients if c.get("expiryTime") and 0 < int(c["expiryTime"]) < now_ms]
    print(f"\n===== 1.0 LIVE clients served NOW: {len(live_10_clients)} "
          f"(disabled_in_live={len(disabled)}, expired_in_live={len(expired)}) =====")

# x-ui.db — configured clients per inbound (compare vs live)
db = None
for p in ("/etc/x-ui/x-ui.db", "/usr/local/x-ui/x-ui.db", "/usr/local/x-ui/bin/x-ui.db"):
    if sh(f"sudo -n test -f {p} && echo y") == "y" or os.path.exists(p):
        db = p
        break
print("\n===== x-ui.db configured clients per inbound =====")
print("db:", db)
if db:
    try:
        conn = sqlite3.connect(db)
    except sqlite3.OperationalError:
        conn = sqlite3.connect("/tmp/_probe10.db")  # fallback after sudo cat below
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id,port,tag,protocol,settings,stream_settings FROM inbounds")
        except sqlite3.OperationalError:
            # permission denied reading the live db — copy bytes out via sudo
            sh(f"sudo -n cat '{db}' > /tmp/_probe10.db 2>/dev/null")
            conn.close()
            conn = sqlite3.connect("/tmp/_probe10.db")
            cur = conn.cursor()
            cur.execute("SELECT id,port,tag,protocol,settings,stream_settings FROM inbounds")
        now_ms = int(time.time() * 1000)
        for iid, port, tag, proto, sj, stj in cur.fetchall():
            try:
                s = json.loads(sj or "{}")
            except Exception:
                s = {}
            try:
                st = json.loads(stj or "{}")
            except Exception:
                st = {}
            sec = st.get("security")
            net = st.get("network")
            clients = s.get("clients", [])
            n_dis = len([c for c in clients if c.get("enable") is False])
            n_exp = len([c for c in clients if c.get("expiryTime") and 0 < int(c["expiryTime"]) < now_ms])
            marker = "  <== 1.0 (tcp/reality)" if (sec == "reality" and net in ("tcp", None)) else ""
            print(f"  inbound id={iid} port={port} tag={tag!r} proto={proto} network={net!r} security={sec!r} "
                  f"clients={len(clients)} disabled={n_dis} expired={n_exp}{marker}")
        conn.close()
    except Exception as e:
        print("  db read error:", e)

print("\n===== recent reality/:443 errors (last 30) =====")
errp = None
if cfg:
    errp = ((cfg.get("log") or {}).get("error"))
if errp:
    print("error log:", errp)
    print(sh(f"sudo -n tail -4000 '{errp}' 2>/dev/null | grep -iE 'reality|443|dial|failed|reset|eof|timeout|invalid' | tail -30") or "(no matching errors)")
else:
    print("(error log path unknown)")
