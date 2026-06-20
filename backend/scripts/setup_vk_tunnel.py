"""Set up the vk-tunnel LTE entry on a whitelisted RU server (84.201.144.227).

Whitelist-bypass method: traffic is tunneled through VK's own edge. vk-tunnel
(npm @vkontakte/vk-tunnel) exposes a LOCAL VLESS+WebSocket inbound under a
VK-assigned public wss:// domain. A client connects vless://...?type=ws to that
VK domain -> VK edge (carrier-whitelisted) -> vk-tunnel here -> the local WS
inbound -> freedom (RU-direct egress). The carrier only ever sees a connection
to VK, so this passes a STRICT whitelist (unlike Reality+SNI camo, which needs
the server IP itself whitelisted).

Runs ON the server via SSH (sudo). Env:
  VKT_LOCAL_PORT   local port the WS inbound listens on + vk-tunnel exposes
                   (default 12345). NOT the client-facing port — clients hit the
                   VK wss domain on :443.
  VKT_WS_PATH      WebSocket path (default /vkt/).
  VKT_PANEL_PASS   panel password. If set, panel creds are reset to
                   bravada/$VKT_PANEL_PASS (bcrypt). Same value goes into the DB
                   row's encrypted_password (pass to add_vk_tunnel_row.py).
  VKT_TUNNEL_TOKEN optional pre-obtained VK tunnel token (from a one-time
                   interactive OAuth). If set, the systemd service runs fully
                   unattended. If unset, the operator must run `vk-tunnel` once
                   interactively to authorize, then restart the service.

Does:
1. Detect the 3x-ui DB (auto-install not done here — the bgg box already has 3x-ui)
2. (optional) reset panel creds -> bravada / $VKT_PANEL_PASS
3. Create a VLESS+WS inbound on $VKT_LOCAL_PORT (security:none — vk-tunnel does TLS)
4. Install Node.js + @vkontakte/vk-tunnel (npm -g)
5. Write a systemd service running vk-tunnel against the local port (Restart=always)
6. Write a domain-extractor (cron each minute) that captures the current wss
   domain from the vk-tunnel journal into /var/lib/vk-tunnel/current-domain, so
   the bot's vpn_servers.tls_sni can be kept in sync (add via relay-setup sync)
7. Print summary: INBOUND_ID, VKT_LOCAL_PORT, VKT_WS_PATH, PANEL_URL + OAuth runbook

After this:
  - Operator does the one-time VK OAuth (run `vk-tunnel` interactively, or supply
    VKT_TUNNEL_TOKEN) + `systemctl enable --now vk-tunnel`.
  - Run add_vk_tunnel_row.py with the printed INBOUND_ID/PANEL_URL + the wss
    domain from /var/lib/vk-tunnel/current-domain.

Caveats:
- One vk-tunnel = one VK account = one tunnel shared by ALL users. Aggregate
  heavy traffic can crash it (block geosite:category-speedtest at routing).
- VK may rotate the wss domain on restart — the extractor + relay-setup sync
  keep vpn_servers.tls_sni current.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys

# x-ui DB location varies by install. Detect at runtime (mirrors setup_relay.py).
DB_CANDIDATES = [
    "/etc/x-ui/x-ui.db",
    "/usr/local/x-ui/x-ui.db",
    "/usr/local/x-ui/bin/x-ui.db",
    "/opt/x-ui/x-ui.db",
]
PANEL_USER = "bravada"
PANEL_PASS = os.environ.get("VKT_PANEL_PASS", "")

VKT_LOCAL_PORT = int(os.environ.get("VKT_LOCAL_PORT", "12345"))
VKT_WS_PATH = os.environ.get("VKT_WS_PATH", "/vkt/")
INBOUND_TAG = "in-vk-tunnel-ws"

CURRENT_DOMAIN_FILE = "/var/lib/vk-tunnel/current-domain"
EXTRACTOR_SCRIPT = "/usr/local/bin/vk-tunnel-domain.sh"
EXTRACTOR_CRON = "/etc/cron.d/vk-tunnel-domain"


def run(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"CMD FAILED: {cmd}\nstderr: {r.stderr}", file=sys.stderr)
    return r


def find_db():
    for p in DB_CANDIDATES:
        if os.path.exists(p):
            return p
    r = run("find /etc /usr/local /opt /root /var/lib -name 'x-ui.db' -type f 2>/dev/null | head -1",
            check=False)
    line = r.stdout.strip().splitlines()
    return line[0] if line else None


def reset_panel_creds(db_path: str) -> None:
    """Reset 3x-ui panel creds via direct bcrypt UPDATE (x-ui setting CLI is
    unreliable on v3.x — interactive menu). Mirrors setup_relay.reset_panel_creds."""

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
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        rows = cur.execute("SELECT id FROM users").fetchall()
        if rows:
            uid = rows[0][0]
            try:
                cur.execute("UPDATE users SET username=?, password=?, login_epoch=0 WHERE id=?",
                            (PANEL_USER, pw_hash, uid))
            except sqlite3.OperationalError:
                cur.execute("UPDATE users SET username=?, password=? WHERE id=?",
                            (PANEL_USER, pw_hash, uid))
        else:
            cur.execute("INSERT INTO users (username, password, login_epoch) VALUES (?, ?, 0)",
                        (PANEL_USER, pw_hash))
        conn.commit()
        print(f"panel creds reset -> username={PANEL_USER}")
    except sqlite3.OperationalError as e:
        # Older 3x-ui: creds in settings keys webUsername/webPassword
        print(f"users table not found ({e}); trying settings keys")
        cur.execute("UPDATE settings SET value=? WHERE key='webUsername'", (PANEL_USER,))
        cur.execute("UPDATE settings SET value=? WHERE key='webPassword'", (pw_hash,))
        conn.commit()
        print(f"settings webUsername/webPassword reset -> {PANEL_USER}")
    conn.close()


# vk-tunnel prints the assigned wss:// domain on connect. The shell extractor
# (write_domain_extractor) greps the journal with this exact character class; keep
# them in sync. Pure extract_wss_domain() below mirrors it for unit testing.
WSS_DOMAIN_RE = re.compile(r"wss://([a-zA-Z0-9._-]+)")


def extract_wss_domain(text: str) -> str | None:
    """Last wss:// domain in vk-tunnel output, or None. Mirrors the shell
    extractor: `grep -oE 'wss://[a-zA-Z0-9._-]+' | tail -1 | sed 's|wss://||'`."""
    matches = WSS_DOMAIN_RE.findall(text)
    return matches[-1] if matches else None


def ws_inbound_stream_settings() -> str:
    """3x-ui streamSettings JSON for the VLESS+WS inbound. Pure (unit-testable).

    security:none because vk-tunnel terminates TLS at the VK edge and forwards
    plain WS to this local inbound. Clients still use security=tls in their link
    (to the VK wss domain) — the mismatch is intended; vk-tunnel bridges it.
    """
    return json.dumps({
        "network": "ws",
        "security": "none",
        "externalProxy": [],
        "wsSettings": {
            "acceptProxyProtocol": False,
            "path": VKT_WS_PATH,
            "host": "",
            "headers": {},
            "heartbeatPeriod": 0,
        },
    })


def create_ws_inbound(db_path: str) -> int:
    """Create the VLESS+WS inbound on VKT_LOCAL_PORT. Returns its inbound id.
    Idempotent: replaces any existing inbound on the same port/tag."""
    settings = json.dumps({"clients": [], "decryption": "none", "fallbacks": []})
    stream = ws_inbound_stream_settings()
    sniffing = json.dumps({"enabled": True, "destOverride": ["http", "tls", "quic"],
                           "metadataOnly": False, "routeOnly": False})

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM inbounds WHERE port=? OR tag=?", (VKT_LOCAL_PORT, INBOUND_TAG))
    cur.execute(
        "INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, "
        "listen, port, protocol, settings, stream_settings, tag, sniffing) "
        "VALUES (0, 0, 0, 0, ?, 1, 0, '', ?, 'vless', ?, ?, ?, ?)",
        ("VK-Tunnel-WS", VKT_LOCAL_PORT, settings, stream, INBOUND_TAG, sniffing),
    )
    conn.commit()
    inbound_id = cur.execute("SELECT id FROM inbounds WHERE tag=?", (INBOUND_TAG,)).fetchone()[0]
    conn.close()
    return inbound_id


def install_vk_tunnel() -> str:
    """Install Node.js + @vkontakte/vk-tunnel globally. Returns the vk-tunnel bin path."""
    print("=== Install Node.js + @vkontakte/vk-tunnel ===")
    run("command -v node || (curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && "
        "apt-get install -y -qq nodejs)", check=False)
    r = run("node -v", check=False)
    print(f"  node: {r.stdout.strip() or 'MISSING'}")
    run("npm install -g @vkontakte/vk-tunnel", check=False)
    # npm global bin is typically /usr/bin/vk-tunnel or /usr/local/bin/vk-tunnel
    r = run("command -v vk-tunnel || npm bin -g 2>/dev/null | xargs -I{} echo {}/vk-tunnel",
            check=False)
    path = r.stdout.strip().splitlines()
    return path[0] if path else "vk-tunnel"


def write_systemd_service(vk_bin: str) -> None:
    """Systemd service that runs vk-tunnel against the local inbound port."""
    token_env = ""
    tok = os.environ.get("VKT_TUNNEL_TOKEN", "").strip()
    if tok:
        token_env = f'\nEnvironment="VKT_TUNNEL_TOKEN={tok}"'
    unit = (
        "[Unit]\n"
        "Description=VK Tunnel (whitelist bypass) -> local VLESS+WS inbound\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={vk_bin} --insecure=1 --http-protocol=https --ws-protocol=wss "
        f"--host=127.0.0.1 --port={VKT_LOCAL_PORT}{token_env}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        # vk-tunnel caches OAuth session under ~/.config/configstore; run as root
        # so the service and the interactive OAuth share the same session file.
        "User=root\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    open("/etc/systemd/system/vk-tunnel.service", "w").write(unit)
    run("systemctl daemon-reload", check=False)
    print("wrote /etc/systemd/system/vk-tunnel.service")


def write_domain_extractor() -> None:
    """Cron job that captures the current wss domain from the vk-tunnel journal
    into /var/lib/vk-tunnel/current-domain. The relay-setup sync step reads this
    file to keep vpn_servers.tls_sni in sync with VK's rotating domain."""
    run(f"mkdir -p {os.path.dirname(CURRENT_DOMAIN_FILE)}", check=False)
    script = (
        "#!/bin/sh\n"
        "# Extract the latest wss:// domain from the vk-tunnel service journal.\n"
        f"DOMAIN=$(journalctl -u vk-tunnel --no-pager -n 500 2>/dev/null "
        "| grep -oE 'wss://[a-zA-Z0-9._-]+' | tail -1 | sed 's|wss://||')\n"
        f'[ -n "$DOMAIN" ] && echo "$DOMAIN" > {CURRENT_DOMAIN_FILE}\n'
    )
    open(EXTRACTOR_SCRIPT, "w").write(script)
    run(f"chmod +x {EXTRACTOR_SCRIPT}", check=False)
    cron = f"* * * * * root {EXTRACTOR_SCRIPT}\n"
    open(EXTRACTOR_CRON, "w").write(cron)
    print(f"wrote domain extractor ({EXTRACTOR_SCRIPT}) + cron ({EXTRACTOR_CRON})")


def panel_url(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    port = cur.execute("SELECT value FROM settings WHERE key='webPort'").fetchone()
    port = port[0] if port else "2053"
    base = cur.execute("SELECT value FROM settings WHERE key='webBasePath'").fetchone()
    base = (base[0].strip("/") if base and base[0] else "").strip("/")
    conn.close()
    host = os.environ.get("VKT_SERVER_HOST", "84.201.144.227")
    return f"https://{host}:{port}/{base}/" if base else f"https://{host}:{port}/"


def main():
    db_path = find_db()
    if not db_path:
        print("ERROR: 3x-ui DB not found. Install 3x-ui on this server first.", file=sys.stderr)
        sys.exit(1)
    print(f"3x-ui DB: {db_path}")

    if PANEL_PASS:
        print("\n=== Reset panel credentials ===")
        reset_panel_creds(db_path)
    else:
        print("\n(panel creds NOT reset — set VKT_PANEL_PASS to reset; the bot must know the password)")

    print("\n=== Create VLESS+WS inbound ===")
    inbound_id = create_ws_inbound(db_path)
    print(f"inbound created: id={inbound_id} port={VKT_LOCAL_PORT} tag={INBOUND_TAG} path={VKT_WS_PATH}")

    vk_bin = install_vk_tunnel()
    print(f"  vk-tunnel bin: {vk_bin}")

    write_systemd_service(vk_bin)
    write_domain_extractor()

    url = panel_url(db_path)

    print("\n" + "=" * 60)
    print("SETUP SUMMARY")
    print("=" * 60)
    print(f"VKT_SERVER_HOST=84.201.144.227")
    print(f"VKT_LOCAL_PORT={VKT_LOCAL_PORT}")
    print(f"VKT_WS_PATH={VKT_WS_PATH}")
    print(f"INBOUND_ID={inbound_id}")
    print(f"PANEL_URL={url}")
    print("=" * 60)
    print("""
NEXT STEPS:
  1. ONE-TIME VK OAuth (the service can't do this unattended):
       systemctl start vk-tunnel   # will print an OAuth URL in the journal
       journalctl -u vk-tunnel -f  # open the auth link, authorize with a VK account
     (or supply VKT_TUNNEL_TOKEN=<token from ~/.config/configstore/@vkontakte/vk-tunnel.json>
      and rerun this script / edit the service's Environment= line)
  2. Enable + start the service + extractor:
       systemctl enable --now vk-tunnel
       sleep 10 && systemctl restart vk-tunnel   # pick up the OAuth session
       /usr/local/bin/vk-tunnel-domain.sh        # prime current-domain
       cat /var/lib/vk-tunnel/current-domain     # <- this is the wss domain for the DB row
  3. From the relay-setup workflow, run add_vk_tunnel_row.py with:
       VKT_INBOUND_ID=<above>  VKT_PANEL_URL=<above>  VKT_PANEL_PASS=<pass>
       VKT_WS_DOMAIN=<from current-domain>
""")


if __name__ == "__main__":
    main()
