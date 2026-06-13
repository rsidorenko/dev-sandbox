"""Set up the RU split-routing relay server (89.169.139.153).

Runs ON the relay server via SSH (sudo). Env (passed from the workflow):
  RELAY_PANEL_PASS   panel password (required)
  HELSINKI_HOST/PORT/PBK/SID/SNI   Helsinki Reality target (optional, defaults below)
  RELAY_UUID         shared relay UUID (optional, default below)

Does:
0. (auto) install 3x-ui if /etc/x-ui/x-ui.db is absent
1. Reset panel creds -> bravada / $RELAY_PANEL_PASS
2. Generate fresh Reality keypair (xray x25519) for this server's own inbound
3. Ensure geoip.dat + geosite.dat present (download from Loyalsoldier if missing)
4. Set xrayTemplateConfig:
     - inbound 443 VLESS+tcp+Reality (clients managed by the bot)
     - outbound `direct` (freedom)
     - outbound `relay-to-helsinki` (VLESS+tcp+Reality -> Helsinki:443, flow="")
     - routing (domainStrategy IPIfNonMatch):
         geoip:private        -> direct
         .ru/.su/.рф + geosite:ru -> direct
         geoip:ru             -> direct
         catch-all tcp,udp    -> relay-to-helsinki
5. Create the 443 inbound (SQLite insert), SNI=max.ru
6. Restart x-ui; verify xray running + :443 listening
7. Print summary (REALITY_PUBKEY, INBOUND_ID, panel URL/port)

After this, register RELAY_UUID on Helsinki:443 (add_helsinki_relay_client.py) and
insert the vpn_servers row (add_relay_server_row.py) using the printed values.

Caveats (from LTE relay work):
- Relay outbound MUST be tcp+reality (xhttp+reality outbound fails with
  "failed to read client hello").
- Do NOT add a top-level "transport" key (Xray 26.x removed it -> crash).
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time

# x-ui DB location varies by install (/etc/x-ui vs /usr/local/x-ui). Detect at runtime.
DB_CANDIDATES = [
    "/etc/x-ui/x-ui.db",
    "/usr/local/x-ui/x-ui.db",
    "/usr/local/x-ui/bin/x-ui.db",
    "/opt/x-ui/x-ui.db",
]
DB_PATH = None  # resolved by ensure_3xui()
XRAY_BIN = "/usr/local/x-ui/bin/xray-linux-amd64"
PANEL_USER = "bravada"
PANEL_PASS = os.environ.get("RELAY_PANEL_PASS", "")

# Helsinki relay target. Defaults = migration 027 seeded values (verified current
# via get_helsinki_keys.py — they back every live Helsinki user link). Overridable
# through env if the DB diverges.
HELSINKI_HOST = os.environ.get("HELSINKI_HOST", "77.221.159.106")
HELSINKI_PORT = int(os.environ.get("HELSINKI_PORT", "443"))
HELSINKI_PBK = os.environ.get("HELSINKI_PBK", "f1m7tkhI4Ez7GlRF7k2E55V86XLsu5jzIphl3yhKgyI")
HELSINKI_SID = os.environ.get("HELSINKI_SID", "37")
HELSINKI_SNI = os.environ.get("HELSINKI_SNI", "eh.vk.ru")

# Shared relay UUID (registered on Helsinki as a client; used by this server's
# relay-to-helsinki outbound). Constant so setup + Helsinki registration stay in sync.
RELAY_UUID = os.environ.get("RELAY_UUID", "00607f0b-a9e7-4280-abb3-2231e1b9c2ff")

# This server's own Reality inbound params.
RELAY_SNI = "max.ru"          # whitelisted by RU mobile DPI (migration 046 precedent)
RELAY_SID = "a1b2c3d4e5f6"
INBOUND_TAG = "in-443-tcp"
INBOUND_PORT = 443

GEO_BASE = "/usr/local/x-ui/bin"
GEO_FILES = {
    "geoip.dat": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat",
    "geosite.dat": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat",
}


def run(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"CMD FAILED: {cmd}\nstderr: {r.stderr}", file=sys.stderr)
    return r


def find_db():
    for p in DB_CANDIDATES:
        if os.path.exists(p):
            return p
    # Broad fallback search (bounded to likely roots).
    r = run("find /etc /usr/local /opt /root /var/lib -name 'x-ui.db' -type f 2>/dev/null | head -1", check=False)
    line = r.stdout.strip().splitlines()
    if line:
        return line[0]
    return None


def ensure_3xui():
    """Resolve the 3x-ui DB path. Only install if xray is NOT already running
    and no DB exists (avoids re-running the installer on an already-installed
    server whose DB lives at a non-default path)."""
    global DB_PATH
    DB_PATH = find_db()
    if DB_PATH:
        print(f"3x-ui DB found: {DB_PATH}")
        return

    xray_running = run("pgrep -f xray-linux-amd64", check=False).returncode == 0
    print("--- diagnostic: what manages xray on this host? ---")
    run("echo '== processes =='; ps -ef | grep -E 'xray|x-ui|sing|marz|hiddify' | grep -v grep", check=False)
    run("echo '== services =='; systemctl list-unit-files 2>/dev/null | grep -iE 'xray|x-ui|sing|hiddify|marz'", check=False)
    run("echo '== listening ports =='; ss -tlnp 2>/dev/null | head -30", check=False)
    run("echo '== /usr/local/x-ui tree =='; ls -laR /usr/local/x-ui 2>/dev/null | head -40", check=False)
    run("echo '== /etc/x-ui =='; ls -la /etc/x-ui 2>/dev/null", check=False)
    run("echo '== find config.json (xray) =='; find / -path /proc -prune -o -path /sys -prune -o "
        "-name 'config.json' -print 2>/dev/null | grep -iE 'xray|x-ui|bin' | head", check=False)
    run("echo '== find any panel .db =='; find / -path /proc -prune -o -path /sys -prune -o "
        "-name '*.db' -print 2>/dev/null | grep -iE 'x-?ui|sing|marz|hiddify|3x' | head", check=False)
    run("echo '== docker? =='; docker ps 2>/dev/null | head; podman ps 2>/dev/null | head", check=False)
    if xray_running:
        print("ERROR: xray is running but no x-ui.db found at known paths. "
              "Locate it and add the path to DB_CANDIDATES.", file=sys.stderr)
        sys.exit(1)

    print("=== No 3x-ui found (no db, xray not running) — installing ===")
    # Official installer is non-interactive when piped (no TTY).
    r = run("bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)", check=False)
    print(f"install rc={r.returncode}")
    DB_PATH = find_db()
    if not DB_PATH:
        print("ERROR: 3x-ui install did not create x-ui.db — install manually first", file=sys.stderr)
        sys.exit(1)
    time.sleep(3)


def reset_panel_creds():
    """Reset 3x-ui panel creds via direct bcrypt UPDATE of the users table.

    The `x-ui setting` CLI is unreliable on v3.x (shows an interactive menu), so
    edit the DB directly so the bot (which logs in as bravada/RELAY_PANEL_PASS)
    can manage clients on this inbound.
    """
    import sqlite3 as _sqlite3

    def _bcrypt_hash(pw):
        try:
            import bcrypt
            return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()
        except ImportError:
            pass
        r = subprocess.run(["htpasswd", "-bnBC", "10", "", pw], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout.split(":", 1)[1].strip()
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "bcrypt"],
                       capture_output=True, text=True, timeout=60)
        import bcrypt
        return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()

    pw_hash = _bcrypt_hash(PANEL_PASS)
    print(f"bcrypt hash generated (len={len(pw_hash)})")

    conn = _sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, username FROM users")
        rows = cur.fetchall()
        if rows:
            uid = rows[0][0]
            try:
                cur.execute("UPDATE users SET username=?, password=?, login_epoch=0 WHERE id=?",
                            (PANEL_USER, pw_hash, uid))
            except _sqlite3.OperationalError:
                cur.execute("UPDATE users SET username=?, password=? WHERE id=?",
                            (PANEL_USER, pw_hash, uid))
            print(f"updated users row id={uid} -> username={PANEL_USER}")
        else:
            cur.execute("INSERT INTO users (username, password, login_epoch) VALUES (?, ?, 0)",
                        (PANEL_USER, pw_hash))
            print(f"inserted users row username={PANEL_USER}")
        conn.commit()
    except _sqlite3.OperationalError as e:
        # Older 3x-ui: creds live in settings keys webUsername/webPassword
        print(f"users table not found ({e}); trying settings keys")
        cur.execute("UPDATE settings SET value=? WHERE key='webUsername'", (PANEL_USER,))
        cur.execute("UPDATE settings SET value=? WHERE key='webPassword'", (pw_hash,))
        conn.commit()
        print("updated settings webUsername/webPassword")
    conn.close()


def ensure_geo_files():
    print("=== Ensure geoip.dat + geosite.dat ===")
    os.makedirs(GEO_BASE, exist_ok=True)
    for name, url in GEO_FILES.items():
        path = os.path.join(GEO_BASE, name)
        if os.path.exists(path) and os.path.getsize(path) > 100_000:
            print(f"  {name}: present ({os.path.getsize(path)} bytes)")
            continue
        print(f"  downloading {name} ...")
        r = run(f"curl -Ls -o {path} {url} && test -s {path}", check=False)
        if r.returncode != 0:
            # fallback to wget
            run(f"wget -q -O {path} {url}", check=False)
        ok = os.path.exists(path) and os.path.getsize(path) > 100_000
        print(f"  {name}: {'OK' if ok else 'MISSING/SMALL'} ({os.path.getsize(path) if os.path.exists(path) else 0} bytes)")
        if not ok:
            print(f"ERROR: could not fetch {name} — geosite:ru/geoip:ru rules need it", file=sys.stderr)
            sys.exit(1)


def main():
    if not PANEL_PASS:
        print("ERROR: RELAY_PANEL_PASS env not set", file=sys.stderr)
        sys.exit(1)

    ensure_3xui()

    # ── Step 1: Reset panel creds (direct bcrypt UPDATE — x-ui setting CLI is
    #   unreliable on v3.x, shows an interactive menu) ──
    print("=== Step 1: Reset panel credentials (bcrypt in users table) ===")
    reset_panel_creds()

    # ── Step 2: Generate Reality keypair ──
    print("\n=== Step 2: Generate Reality keypair ===")
    r = run(f"sudo {XRAY_BIN} x25519")
    m = re.search(r"PrivateKey:\s*(\S+)", r.stdout)
    priv = m.group(1) if m else ""
    m2 = re.search(r"(?:PublicKey|Password).*?:\s*(\S+)", r.stdout)
    pub = m2.group(1) if m2 else ""
    print(f"privateKey={priv}")
    print(f"publicKey={pub}")
    if not priv or not pub:
        print("ERROR: could not parse x25519 output", file=sys.stderr)
        sys.exit(1)

    # ── Step 3: Geo files ──
    ensure_geo_files()

    # ── Step 4: Build & set xrayTemplateConfig ──
    print("\n=== Step 4: Set xrayTemplateConfig (split routing + Helsinki relay) ===")
    template = {
        "log": {"loglevel": "warning", "access": "/var/log/xray-access.log",
                "error": "/var/log/xray-error.log", "dnsLog": False, "maskAddress": ""},
        "api": {"services": ["HandlerService", "LoggerService", "StatsService"],
                "tag": "api"},
        "stats": {},
        "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
                   "system": {"statsInboundUplink": True, "statsInboundDownlink": True,
                              "statsOutboundUplink": True, "statsOutboundDownlink": True}},
        "routing": {
            # IPIfNonMatch: try domain rules first; only resolve to IP (for geoip:ru)
            # when no domain rule matched. Lets RU domains skip DNS, foreign domains
            # still get geoip-checked before falling through to the Helsinki relay.
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"},
                # Russian domains -> direct (xn--p1ai is the punycode for .рф)
                {"type": "field", "inboundTag": [INBOUND_TAG],
                 "domain": ["geosite:ru", "domain:ru", "domain:su", "domain:xn--p1ai"],
                 "outboundTag": "direct"},
                # Connections by IP to Russian networks -> direct
                {"type": "field", "inboundTag": [INBOUND_TAG],
                 "ip": ["geoip:ru"], "outboundTag": "direct"},
                # Everything else -> Helsinki
                {"type": "field", "inboundTag": [INBOUND_TAG],
                 "network": "tcp,udp", "outboundTag": "relay-to-helsinki"},
            ],
        },
        "inbounds": [{"tag": "api", "listen": "127.0.0.1", "port": 62789,
                      "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}}],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "relay-to-helsinki", "protocol": "vless",
             "settings": {"vnext": [{"address": HELSINKI_HOST, "port": HELSINKI_PORT,
                                     "users": [{"id": RELAY_UUID, "encryption": "none", "flow": ""}]}]},
             "streamSettings": {
                 "network": "tcp", "security": "reality",
                 "realitySettings": {"serverName": HELSINKI_SNI, "fingerprint": "chrome",
                                     "publicKey": HELSINKI_PBK, "shortId": HELSINKI_SID},
                 "tcpSettings": {"header": {"type": "none"}}}},
            {"tag": "blocked", "protocol": "blackhole"},
        ],
    }
    # NOTE: no top-level "transport" key (Xray 26.x removed it -> startup crash).
    # NOTE: relay = tcp+reality (xhttp+reality outbound fails on the Helsinki side).

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    template_json = json.dumps(template)
    cur.execute("SELECT count(*) FROM settings WHERE key='xrayTemplateConfig'")
    if cur.fetchone()[0] > 0:
        cur.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'", (template_json,))
    else:
        cur.execute("INSERT INTO settings(key, value) VALUES('xrayTemplateConfig', ?)", (template_json,))
    conn.commit()
    print("xrayTemplateConfig written to DB")

    # ── Step 5: Create VLESS+reality inbound on 443 ──
    print("\n=== Step 5: Create inbound 443 ===")
    settings = json.dumps({"clients": [], "decryption": "none", "fallbacks": []})
    stream_settings = json.dumps({
        "network": "tcp", "security": "reality",
        "externalProxy": [],
        "realitySettings": {
            "show": False, "xver": 0, "dest": "127.0.0.1:10443",
            "serverNames": [RELAY_SNI], "privateKey": priv,
            "minClientVer": "", "maxClientVer": "", "maxTimeDiff": 0,
            "shortIds": [RELAY_SID, "37", "", "6ba8", "a1b2c3"],
            "settings": {"publicKey": pub, "fingerprint": "chrome", "serverName": "", "spiderX": "/"},
        },
        "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
    })
    sniffing = json.dumps({"enabled": True, "destOverride": ["http", "tls", "quic"],
                           "metadataOnly": False, "routeOnly": False})

    cur.execute("DELETE FROM inbounds WHERE port=? OR tag=?", (INBOUND_PORT, INBOUND_TAG))
    cur.execute(
        "INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, "
        "listen, port, protocol, settings, stream_settings, tag, sniffing) "
        "VALUES (0, 0, 0, 0, ?, 1, 0, '', ?, 'vless', ?, ?, ?, ?)",
        ("RU-Relay-Reality-443", INBOUND_PORT, settings, stream_settings, INBOUND_TAG, sniffing)
    )
    conn.commit()
    inbound_id = cur.execute("SELECT id FROM inbounds WHERE tag=?", (INBOUND_TAG,)).fetchone()[0]
    print(f"inbound created: id={inbound_id} port={INBOUND_PORT} tag={INBOUND_TAG}")

    # Read panel port + webBasePath for the panel URL the bot uses.
    panel_port = cur.execute("SELECT value FROM settings WHERE key='webPort'").fetchone()
    panel_port = panel_port[0] if panel_port else "2053"
    web_base = cur.execute("SELECT value FROM settings WHERE key='webBasePath'").fetchone()
    web_base = (web_base[0].strip("/") if web_base and web_base[0] else "").strip("/")
    conn.close()

    # ── Step 6: Restart x-ui ──
    print("\n=== Step 6: Restart x-ui ===")
    r = run("sudo systemctl restart x-ui", check=False)
    print(f"restart: rc={r.returncode}")
    time.sleep(6)

    r = run("pgrep -f xray-linux-amd64", check=False)
    xray_ok = r.returncode == 0
    print(f"xray running: {xray_ok} pid={r.stdout.strip()}")

    r = run(f"sudo grep -c 'relay-to-helsinki' /usr/local/x-ui/bin/config.json", check=False)
    print(f"config.json relay mentions: {r.stdout.strip()}")
    r = run("sudo ss -tlnp | grep -E ':443 '", check=False)
    print(f"listening:\n{r.stdout.strip()}")

    panel_url = f"https://89.169.139.153:{panel_port}/{web_base}/" if web_base else f"https://89.169.139.153:{panel_port}/"

    # ── Step 7: Summary ──
    print("\n" + "=" * 60)
    print("SETUP SUMMARY")
    print("=" * 60)
    print(f"PANEL_USER={PANEL_USER}")
    print(f"REALITY_PUBKEY={pub}")
    print(f"REALITY_PRIVKEY={priv}")
    print(f"INBOUND_ID={inbound_id}")
    print(f"RELAY_UUID={RELAY_UUID}")
    print(f"SERVER_HOST=89.169.139.153")
    print(f"PANEL_URL={panel_url}")
    print(f"HELSINKI_TARGET={HELSINKI_HOST}:{HELSINKI_PORT} sni={HELSINKI_SNI}")
    print("=" * 60)
    print("""
NEXT STEPS (run from the relay-setup workflow):
  1. add_helsinki_relay=true   -> register RELAY_UUID on Helsinki :443
  2. add_relay_row=true        -> insert vpn_servers row with:
        RELAY_PUBKEY=%s
        RELAY_INBOUND_ID=%s
        RELAY_PANEL_URL=%s
""" % (pub, inbound_id, panel_url))


if __name__ == "__main__":
    main()
