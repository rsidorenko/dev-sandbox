"""Combined LTE setup: reset panel + Reality keys + xrayTemplateConfig + inbound 443.

Runs ON LTE via SSH (sudo). Env: LTE_PANEL_PASS (from GitHub secret).

Does:
1. Reset panel username='bravada' password=$LTE_PANEL_PASS via x-ui CLI
2. Generate fresh Reality keypair (xray x25519)
3. Generate relay UUID
4. Set xrayTemplateConfig (relay-to-frankfurt outbound via xhttp+reality, routing)
5. Create VLESS+reality inbound on port 443 (SQLite insert)
6. Restart x-ui
7. Print summary: REALITY_PUBKEY, INBOUND_ID, RELAY_UUID, PANEL_USER

Frankfurt target (hardcoded, already exists):
- 77.110.100.210:8443, xhttp+reality
- publicKey: Q_wpt7L8sU2O1OVBV-mpsSvgLAChIhN4hgTm0XZH4Do
- shortId: a1b2c3d4e5f6, serverName: vk.com
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
XRAY_BIN = "/usr/local/x-ui/bin/xray-linux-amd64.real"
PANEL_USER = "bravada"
PANEL_PASS = os.environ.get("LTE_PANEL_PASS", "")

# Frankfurt relay target
FRANKFURT_HOST = "77.110.100.210"
FRANKFURT_PORT = 8443
FRANKFURT_PBK = "Q_wpt7L8sU2O1OVBV-mpsSvgLAChIhN4hgTm0XZH4Do"
FRANKFURT_SID = "a1b2c3d4e5f6"
FRANKFURT_SNI = "vk.com"

INBOUND_TAG = "in-443-tcp"
INBOUND_PORT = 443


def run(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"CMD FAILED: {cmd}\nstderr: {r.stderr}", file=sys.stderr)
    return r


def main():
    if not PANEL_PASS:
        print("ERROR: LTE_PANEL_PASS env not set", file=sys.stderr)
        sys.exit(1)

    # ── Step 1: Reset panel creds ──
    print("=== Step 1: Reset panel credentials ===")
    r = run(f"sudo x-ui setting -username {PANEL_USER} -password '{PANEL_PASS}'", check=False)
    print(f"x-ui setting: rc={r.returncode} out={r.stdout.strip()[:80]}")

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
            "domainStrategy": "AsIs",
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"},
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
                 "network": "xhttp", "security": "reality",
                 "realitySettings": {"serverName": FRANKFURT_SNI, "fingerprint": "chrome",
                                     "publicKey": FRANKFURT_PBK, "shortId": FRANKFURT_SID},
                 "xhttpSettings": {"host": FRANKFURT_SNI, "path": "/"}},
             "mux": {"enabled": True, "concurrency": 8}},
            {"tag": "blocked", "protocol": "blackhole"},
        ],
        "transport": {"services": [{"type": "http", "tag": "metrics_in",
                     "listen": "127.0.0.1", "port": 21101}]},
    }

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
    settings = json.dumps({"clients": [], "decryption": "none", "fallbacks": []})
    stream_settings = json.dumps({
        "network": "tcp", "security": "reality",
        "externalProxy": [],
        "realitySettings": {
            "show": False, "xver": 0, "dest": "vk.com:443",
            "serverNames": ["vk.com"], "privateKey": priv,
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
    r = run("sudo ss -tlnp | grep -E ':443 |:54023 '", check=False)
    print(f"listening ports:\n{r.stdout.strip()}")

    # ── Step 7: Summary ──
    print("\n" + "=" * 60)
    print("SETUP SUMMARY (copy these for DB/Frankfurt steps):")
    print("=" * 60)
    print(f"PANEL_USER={PANEL_USER}")
    print(f"REALITY_PUBKEY={pub}")
    print(f"REALITY_PRIVKEY={priv}")
    print(f"INBOUND_ID={inbound_id}")
    print(f"RELAY_UUID={relay_uuid}")
    print(f"SERVER_HOST=62.84.118.140")
    print("=" * 60)


if __name__ == "__main__":
    main()
