"""Create a VLESS+WebSocket ORIGIN inbound on a 3x-ui panel for the MWS (МТС Web
Services) CDN front.

Architecture:
  client (vless://ws+tls) -> MWS CDN edge topXXXXXXXX.mwscdn.ru:443 (AS8359 МТС,
  hopefully whitelisted during ЧС) -> THIS origin (plain WS, security:none) -> freedom.

MWS terminates TLS to the client and forwards plain WebSocket to this local
inbound (like Cloudflare WS fronting, but on a RU operator CDN whose edge may
survive the mobile whitelist). This script creates that origin inbound + one
test client, then restarts x-ui so it is served.

Idempotent: replaces any existing inbound on the same port/tag.

Runs ON the panel via SSH (sudo). Env:
  MWS_PORT        origin ws port (default 20999)
  MWS_PATH        ws path (default /mws)
  MWS_TEST_UUID   test client uuid (default: random)
  MWS_HOSTNAME    client-facing MWS hostname, e.g. top2099619178.mwscdn.ru
                  (only used to print the ready-to-test vless link)
  MWS_PANEL_HOST  panel host for the printed PANEL_URL (default: auto from SSH)

After this, run sync_clients_table on the panel (relay-setup sync_tables) so the
test client is mirrored into the v3 clients table (xray reads that) — then the
printed test link works in Happ/v2rayNG.
"""

import json
import os
import sqlite3
import subprocess
import sys
import uuid

# x-ui DB location varies by install. Detect at runtime (mirrors setup_vk_tunnel.py).
DB_CANDIDATES = [
    "/etc/x-ui/x-ui.db",
    "/usr/local/x-ui/x-ui.db",
    "/usr/local/x-ui/bin/x-ui.db",
    "/opt/x-ui/x-ui.db",
]

MWS_PORT = int(os.environ.get("MWS_PORT", "20999"))
MWS_PATH = os.environ.get("MWS_PATH", "/mws")
INBOUND_TAG = "in-mws-ws"
TEST_UUID = os.environ.get("MWS_TEST_UUID") or str(uuid.uuid4())


def run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"CMD FAILED: {cmd}\nstderr: {r.stderr}", file=sys.stderr)
    return r


def find_db() -> str | None:
    for p in DB_CANDIDATES:
        if os.path.exists(p):
            return p
    r = run(
        "find /etc /usr/local /opt /root /var/lib -name 'x-ui.db' -type f 2>/dev/null | head -1",
        check=False,
    )
    line = r.stdout.strip().splitlines()
    return line[0] if line else None


def ws_inbound_stream_settings() -> str:
    """3x-ui streamSettings JSON for the VLESS+WS origin inbound. Pure (unit-testable).

    security:none because the MWS edge terminates TLS to the client and forwards
    plain WS here. Clients still use security=tls in their link (to the MWS
    hostname); the mismatch is intended — MWS bridges it.
    """
    return json.dumps({
        "network": "ws",
        "security": "none",
        "externalProxy": [],
        "wsSettings": {
            "acceptProxyProtocol": False,
            "path": MWS_PATH,
            "host": "",
            "headers": {},
            "heartbeatPeriod": 0,
        },
    })


def create_inbound_with_test_client(db_path: str) -> int:
    """Create the VLESS+WS origin inbound with one test client. Returns inbound id.

    Idempotent: replaces any existing inbound on the same port/tag.
    """
    client = {
        "id": TEST_UUID,
        "email": "test-mws",
        "enable": True,
        "expiryTime": 0,
        "flow": "",
        "limitIp": 0,
        "totalGB": 0,
        "tgId": "",
        "subId": "",
    }
    settings = json.dumps({"clients": [client], "decryption": "none", "fallbacks": []})
    stream = ws_inbound_stream_settings()
    sniffing = json.dumps({
        "enabled": True,
        "destOverride": ["http", "tls", "quic"],
        "metadataOnly": False,
        "routeOnly": False,
    })

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM inbounds WHERE port=? OR tag=?", (MWS_PORT, INBOUND_TAG))
    cur.execute(
        "INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, "
        "listen, port, protocol, settings, stream_settings, tag, sniffing) "
        "VALUES (0, 0, 0, 0, ?, 1, 0, '', ?, 'vless', ?, ?, ?, ?)",
        ("MWS-CDN-WS", MWS_PORT, settings, stream, INBOUND_TAG, sniffing),
    )
    conn.commit()
    inbound_id = cur.execute("SELECT id FROM inbounds WHERE tag=?", (INBOUND_TAG,)).fetchone()[0]
    conn.close()
    return inbound_id


def panel_url(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    port = cur.execute("SELECT value FROM settings WHERE key='webPort'").fetchone()
    port = port[0] if port else "2053"
    base = cur.execute("SELECT value FROM settings WHERE key='webBasePath'").fetchone()
    base = (base[0].strip("/") if base and base[0] else "").strip("/")
    conn.close()
    host = os.environ.get("MWS_PANEL_HOST", "")
    if not host:
        # best-effort: use the box's primary IPv4
        r = run("hostname -I 2>/dev/null | awk '{print $1}'", check=False)
        host = r.stdout.strip() or "ORIGIN_IP"
    return f"https://{host}:{port}/{base}/" if base else f"https://{host}:{port}/"


def restart_xui() -> None:
    run("x-ui restart 2>/dev/null || systemctl restart x-ui 2>/dev/null || true", check=False)
    # confirm xray is up
    r = run("pgrep -f xray >/dev/null && echo UP || echo DOWN", check=False)
    print(f"  xray after restart: {r.stdout.strip()}")


def main() -> None:
    db_path = find_db()
    if not db_path:
        print("ERROR: 3x-ui DB not found. Install 3x-ui on this server first.", file=sys.stderr)
        sys.exit(1)
    print(f"3x-ui DB: {db_path}")

    print("\n=== Create VLESS+WS origin inbound (+ test client) ===")
    inbound_id = create_inbound_with_test_client(db_path)
    print(f"inbound id={inbound_id} port={MWS_PORT} path={MWS_PATH} test_uuid={TEST_UUID}")

    print("\n=== Restart x-ui ===")
    restart_xui()

    url = panel_url(db_path)
    hostname = os.environ.get("MWS_HOSTNAME", "MWS_HOSTNAME")
    path_clean = MWS_PATH.strip("/")
    test_link = (
        f"vless://{TEST_UUID}@{hostname}:443"
        f"?security=tls&type=ws&path=%2F{path_clean}"
        f"&host={hostname}&sni={hostname}&fp=chrome#MWS-test"
    )

    print("\n" + "=" * 60)
    print("SETUP SUMMARY")
    print("=" * 60)
    print(f"MWS_PORT={MWS_PORT}")
    print(f"MWS_PATH={MWS_PATH}")
    print(f"INBOUND_ID={inbound_id}")
    print(f"TEST_UUID={TEST_UUID}")
    print(f"PANEL_URL={url}")
    print(f"\nTEST LINK (import into Happ — needs sync_tables first):")
    print(test_link)
    print("=" * 60)
    print("""
NEXT:
  1. From relay-setup run sync_tables (Frankfurt) so the test client is mirrored
     into the v3 clients table (xray reads it) + x-ui restarts.
  2. Import the TEST LINK into Happ -> connect -> open ifconfig.me.
     Should show THIS origin's IP (e.g. Frankfurt 77.110.100.210).
  3. If it works -> add_mws_row.py with INBOUND_ID + PANEL creds + the MWS hostname,
     then reprovision_active to provision all users.
""")


if __name__ == "__main__":
    main()
