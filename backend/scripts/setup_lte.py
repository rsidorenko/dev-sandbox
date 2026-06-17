"""Combined LTE setup: reset panel + Reality keys + xrayTemplateConfig + inbound 443.

Runs ON LTE via SSH (sudo). Env: LTE_PANEL_PASS (from GitHub secret).

Does:
1. Reset panel username='bravada' password=$LTE_PANEL_PASS via x-ui CLI
2. Generate fresh Reality keypair (xray x25519)
3. Generate relay UUID
4. Set xrayTemplateConfig (RU→direct egress from this RU/Yandex IP; foreign→
   relay-to-frankfurt via TCP+reality; domainStrategy=IPIfNonMatch)
5. Create VLESS+reality inbound on port 443 (SQLite insert)
6. Restart x-ui
7. Print summary: REALITY_PUBKEY, INBOUND_ID, RELAY_UUID, PANEL_USER

Frankfurt target (hardcoded, already exists):
- 77.110.100.210:443, TCP+reality
- publicKey: Q_wpt7L8sU2O1OVBV-mpsSvgLAChIhN4hgTm0XZH4Do
- shortId: a1b2c3d4e5f6, serverName: mgg.bravada-connect.online

IMPORTANT: After running this script, you MUST also register the relay UUID
on Frankfurt's side (Step 8 in the printed summary). The UUID must be added to
Frankfurt's `clients` table AND `client_inbounds` table in /etc/x-ui/x-ui.db,
then xray restarted on Frankfurt.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid

DB_PATH = "/etc/x-ui/x-ui.db"
XRAY_BIN = "/usr/local/x-ui/bin/xray-linux-amd64"
PANEL_USER = "bravada"
PANEL_PASS = os.environ.get("LTE_PANEL_PASS", "")

# Frankfurt relay target — uses TCP+Reality on port 443
# (NOT XHTTP — XHTTP+Reality outbound fails with "failed to read client hello")
FRANKFURT_HOST = "77.110.100.210"
FRANKFURT_PORT = 443
FRANKFURT_PBK = "Q_wpt7L8sU2O1OVBV-mpsSvgLAChIhN4hgTm0XZH4Do"
FRANKFURT_SID = "a1b2c3d4e5f6"
FRANKFURT_SNI = "mgg.bravada-connect.online"

# LTE inbound SNI (this server's own domain)
LTE_SNI = "bgg.bravada-connect.online"

INBOUND_TAG = "in-443-tcp"
INBOUND_PORT = 443

# RU TLDs routed to `direct` (egress from this server's own RU IP) instead of
# Frankfurt. Mirrors manage_ru_egress.RU_DOMAINS.
RU_DOMAINS = ["domain:ru", "domain:su", "domain:xn--p1ai"]


def run(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"CMD FAILED: {cmd}\nstderr: {r.stderr}", file=sys.stderr)
    return r


def setup_camo(sni: str) -> str:
    """Reality camouflage: a Let's Encrypt cert for `sni` served by nginx on
    127.0.0.1:10443 (mirrors Frankfurt/Helsinki). Returns the Reality `dest`.

    On success -> "127.0.0.1:10443" (proper camo: cert matches the SNI).
    Falls back to "yandex.ru:443" (a reachable external TLS target) if certbot
    can't obtain a cert — e.g. the SNI's DNS isn't pointing at this server yet,
    or inbound :80 is closed in the cloud SG (HTTP-01 challenge). The fallback
    keeps Reality working (handshake borrows yandex's TLS); camo quality is
    lower but function is intact. Re-run after DNS/:80 are ready to upgrade.

    Requires (for the proper path): `sni` DNS -> this server, and inbound :80
    open (cloud SG + no OS firewall). Run this script as root."""
    print(f"\n=== Step: Reality camo (LE cert + nginx on 10443 for {sni}) ===")
    run("sudo apt-get install -y -qq nginx certbot", check=False)
    run("sudo systemctl stop nginx", check=False)  # free :80 for certbot --standalone
    r = run(f"sudo certbot certonly --standalone --non-interactive --agree-tos "
            f"-m admin@bravada-connect.ru -d {sni}", check=False)
    cert_dir = f"/etc/letsencrypt/live/{sni}"
    if r.returncode != 0 or not os.path.isdir(cert_dir):
        print("  certbot failed (DNS not at this server yet? :80 closed in cloud SG?) "
              "-> fallback dest=yandex.ru:443 (camo uses yandex cert; re-run later to upgrade)")
        return "yandex.ru:443"
    site = (
        "server {\n"
        "    listen 127.0.0.1:10443 ssl;\n"
        f"    server_name {sni};\n"
        f"    ssl_certificate {cert_dir}/fullchain.pem;\n"
        f"    ssl_certificate_key {cert_dir}/privkey.pem;\n"
        "    ssl_protocols TLSv1.3;\n"
        "    location / { return 200 'OK'; add_header Content-Type text/plain; }\n"
        "}\n"
    )
    open("/etc/nginx/sites-available/le-tls", "w").write(site)
    try:
        if not os.path.exists("/etc/nginx/sites-enabled/le-tls"):
            os.symlink("/etc/nginx/sites-available/le-tls", "/etc/nginx/sites-enabled/le-tls")
    except FileExistsError:
        pass
    if os.path.exists("/etc/nginx/sites-enabled/default"):
        os.remove("/etc/nginx/sites-enabled/default")
    if run("sudo nginx -t", check=False).returncode == 0:
        run("sudo systemctl start nginx", check=False)
        print(f"  nginx serving {sni} cert on 127.0.0.1:10443 -> dest=127.0.0.1:10443 (proper camo)")
        return "127.0.0.1:10443"
    print("  nginx -t failed -> fallback dest=yandex.ru:443")
    return "yandex.ru:443"



def main():
    # PANEL_PASS is only required when LTE_RESET_PANEL=1 (default: keep existing
    # panel credentials, e.g. the ones set during the 3x-ui panel install).
    reset_panel = os.environ.get("LTE_RESET_PANEL") == "1"
    if reset_panel and not PANEL_PASS:
        print("ERROR: LTE_RESET_PANEL=1 but LTE_PANEL_PASS not set", file=sys.stderr)
        sys.exit(1)

    # ── Step 1: Reset panel creds (optional) ──
    if reset_panel:
        print("=== Step 1: Reset panel credentials ===")
        r = run(f"sudo x-ui setting -username {PANEL_USER} -password '{PANEL_PASS}'", check=False)
        print(f"x-ui setting: rc={r.returncode} out={r.stdout.strip()[:80]}")
    else:
        print("=== Step 1: SKIPPED (keep existing creds; set LTE_RESET_PANEL=1 to reset) ===")

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

    # ── Step 3: Generate relay UUID ──
    relay_uuid = str(uuid.uuid4())
    print(f"\n=== Step 3: Relay UUID ===\n{relay_uuid}")

    # ── Step 4: Build & set xrayTemplateConfig ──
    print("\n=== Step 4: Set xrayTemplateConfig ===")
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
            # IPIfNonMatch so geoip:ru resolves domain-based connections to IPs
            # before matching (matches manage_ru_egress).
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"},
                # RU traffic egresses DIRECTLY from this server (a Yandex RU IP),
                # NOT via Frankfurt — so RU apps/services see a RU source IP.
                {"type": "field", "domain": RU_DOMAINS, "outboundTag": "direct"},
                {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"},
                # Everything else -> Frankfurt (foreign egress).
                {"type": "field", "inboundTag": [INBOUND_TAG],
                 "network": "tcp,udp", "outboundTag": "relay-to-frankfurt"},
            ],
        },
        "inbounds": [{"tag": "api", "listen": "127.0.0.1", "port": 62789,
                      "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}}],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "relay-to-frankfurt", "protocol": "vless",
             "settings": {"vnext": [{"address": FRANKFURT_HOST, "port": FRANKFURT_PORT,
                                     "users": [{"id": relay_uuid, "encryption": "none", "flow": ""}]}]},
             "streamSettings": {
                 "network": "tcp", "security": "reality",
                 "realitySettings": {"serverName": FRANKFURT_SNI, "fingerprint": "chrome",
                                     "publicKey": FRANKFURT_PBK, "shortId": FRANKFURT_SID},
                 "tcpSettings": {"header": {"type": "none"}}}},
            {"tag": "blocked", "protocol": "blackhole"},
        ],
    }
    # NOTE: Xray 26.x removed top-level "transport" config.
    # Do NOT add "transport" key — it will crash xray on startup with:
    # "The feature Global transport config has been removed"
    #
    # NOTE: Relay uses TCP+Reality (not XHTTP+Reality). XHTTP outbound fails with
    # "failed to read client hello" on the Frankfurt side. TCP works reliably.

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    template_json = json.dumps(template)
    # Upsert xrayTemplateConfig
    cur.execute("SELECT count(*) FROM settings WHERE key='xrayTemplateConfig'")
    if cur.fetchone()[0] > 0:
        cur.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'", (template_json,))
    else:
        cur.execute("INSERT INTO settings(key, value) VALUES('xrayTemplateConfig', ?)", (template_json,))
    conn.commit()
    print("xrayTemplateConfig written to DB")

    # ── Step 5: Create VLESS+reality inbound on 443 ──
    print("\n=== Step 5: Create inbound 443 ===")
    # Reality camo dest: proper LE cert + nginx on 10443 when DNS/:80 are ready,
    # else a reachable external TLS target (yandex.ru:443) so the handshake works.
    dest = setup_camo(LTE_SNI)
    settings = json.dumps({"clients": [], "decryption": "none", "fallbacks": []})
    stream_settings = json.dumps({
        "network": "tcp", "security": "reality",
        "externalProxy": [],
        "realitySettings": {
            # dest: set by setup_camo() above — 127.0.0.1:10443 (nginx + LE cert
            # for the SNI, proper camo) when available, else yandex.ru:443 (a
            # reachable external TLS target so the handshake still works). MUST be
            # reachable or every handshake fails ("REALITY: failed to dial dest").
            "show": False, "xver": 0, "dest": dest,
            "serverNames": [LTE_SNI], "privateKey": priv,
            "minClientVer": "", "maxClientVer": "", "maxTimeDiff": 0,
            "shortIds": ["a1b2c3d4e5f6", "37", "", "6ba8", "a1b2c3"],
            "settings": {"publicKey": pub, "fingerprint": "chrome", "serverName": "", "spiderX": "/"},
        },
        "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
    })
    sniffing = json.dumps({"enabled": True, "destOverride": ["http", "tls", "quic"],
                           "metadataOnly": False, "routeOnly": False})

    # Remove existing inbound on 443 if any, then insert
    cur.execute("DELETE FROM inbounds WHERE port=? OR tag=?", (INBOUND_PORT, INBOUND_TAG))
    cur.execute(
        "INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, "
        "listen, port, protocol, settings, stream_settings, tag, sniffing) "
        "VALUES (0, 0, 0, 0, ?, 1, 0, '', ?, 'vless', ?, ?, ?, ?)",
        ("LTE-Reality-443", INBOUND_PORT, settings, stream_settings, INBOUND_TAG, sniffing)
    )
    conn.commit()
    inbound_id = cur.execute("SELECT id FROM inbounds WHERE tag=?", (INBOUND_TAG,)).fetchone()[0]
    print(f"inbound created: id={inbound_id} port={INBOUND_PORT} tag={INBOUND_TAG}")
    conn.close()

    # ── Step 6: Restart x-ui ──
    print("\n=== Step 6: Restart x-ui ===")
    r = run("sudo systemctl restart x-ui", check=False)
    print(f"restart: rc={r.returncode}")
    time.sleep(6)

    # Verify xray running
    r = run("pgrep -f xray-linux-amd64", check=False)
    xray_ok = r.returncode == 0
    print(f"xray running: {xray_ok} pid={r.stdout.strip()}")

    # Verify config.json has the pieces
    r = run(f"sudo grep -c 'relay-to-frankfurt' /usr/local/x-ui/bin/config.json", check=False)
    print(f"config.json relay mentions: {r.stdout.strip()}")
    r = run("sudo ss -tlnp | grep -E ':443 '", check=False)
    print(f"listening ports:\n{r.stdout.strip()}")

    # ── Step 7: Summary ──
    print("\n" + "=" * 60)
    print("SETUP SUMMARY")
    print("=" * 60)
    print(f"PANEL_USER={PANEL_USER}")
    print(f"REALITY_PUBKEY={pub}")
    print(f"REALITY_PRIVKEY={priv}")
    print(f"INBOUND_ID={inbound_id}")
    print(f"RELAY_UUID={relay_uuid}")
    print(f"SERVER_HOST=158.160.221.185")
    print("=" * 60)
    print("\n" + "!" * 60)
    print("IMPORTANT: Register relay UUID on Frankfurt!")
    print("!" * 60)
    print(f"""
Run on Frankfurt (77.110.100.210):

  sudo python3 -c '
import sqlite3, json, time
RELAY_UUID = "{relay_uuid}"
RELAY_EMAIL = "relay-from-lte"
INBOUND_ID = 1  # Frankfurt port 443 inbound
c = sqlite3.connect("/etc/x-ui/x-ui.db")
cur = c.cursor()
# 1. Add to clients table
now = int(time.time())
cur.execute("INSERT INTO clients (email,uuid,enable,flow,limit_ip,total_gb,expiry_time,reset,created_at,updated_at) VALUES (?,?,?,?,0,0,0,0,?,?)",
    (RELAY_EMAIL, RELAY_UUID, 1, "", now, now))
client_id = cur.lastrowid
# 2. Link to inbound
cur.execute("INSERT INTO client_inbounds (client_id,inbound_id) VALUES (?,?)", (client_id, INBOUND_ID))
# 3. Also add to inbound settings JSON
row = cur.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,)).fetchone()
settings = json.loads(row[0])
settings.setdefault("clients", []).append({{"id": RELAY_UUID, "email": RELAY_EMAIL, "enable": True}})
cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), INBOUND_ID))
c.commit(); c.close()
print("Relay client registered, id=" + str(client_id))
  '
  sudo systemctl restart x-ui
""")


if __name__ == "__main__":
    main()
