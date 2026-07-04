"""Insert vpn_servers rows for the bravada2 Cloud.ru relay entry (176.123.167.72 → Helsinki).

Second bare_xray RU entry (Sbercloud AS208677, same clean-AS as Cloud.ru), egressing via
Helsinki (77.221.159.106). Gives Happ users a Helsinki-egress variant alongside Cloud.ru's
Frankfurt egress. Same architecture: bare xray, bravada2 camo, 2-hop relay (RU entry →
Helsinki), live gRPC management (XrayApiClient per-row SSH creds).

Run AFTER the bravada2 server setup (xray + camo + routing → Helsinki + gRPC) produces the
Reality keypair. Takes the generated pbk/sid from env:
  BRAVADA2_PBK  — xray x25519 public key (from the server setup)
  BRAVADA2_SID  — reality shortId (from the server setup)

Runs in the production container with DATABASE_URL. Idempotent.
"""

import asyncio
import os
import sys

BRAVADA2_HOST = "176.123.167.72"
BRAVADA2_SNI = "bravada2.bravada-connect.online"
SSH_USER = "romtan"
SSH_KEY_PATH = "/opt/bravada/helsinki_entry_key.pem"
XHTTP_PATH = "/bravada2-xhttp"


async def run() -> None:
    pbk = os.environ.get("BRAVADA2_PBK", "").strip()
    sid = os.environ.get("BRAVADA2_SID", "").strip()
    if not pbk or not sid:
        print("ERROR: set BRAVADA2_PBK and BRAVADA2_SID env (from the xray setup on bravada2)", file=sys.stderr)
        sys.exit(1)
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        for row_id, label, port, transport, tag, ws_path in (
            (28, "Финляндия 1.0(Happ)", 443, "tcp", "bravada2-tcp", "/ws"),
            (29, "Финляндия 2.0(Happ)", 8443, "xhttp", "bravada2-xhttp", XHTTP_PATH),
        ):
            existing = await pool.fetchrow("SELECT server_host FROM vpn_servers WHERE id=$1", row_id)
            if existing and existing["server_host"] != BRAVADA2_HOST:
                print(f"ERROR: id={row_id} used by {existing['server_host']!r}", file=sys.stderr)
                sys.exit(1)
            await pool.execute("DELETE FROM vpn_servers WHERE id=$1", row_id)
            await pool.execute(
                """
                INSERT INTO vpn_servers (
                    id, label, country_code, country_flag, server_host, server_port,
                    ws_path, tls_sni, panel_url, panel_username, panel_password,
                    encrypted_password, inbound_id, reality_pbk, reality_sid,
                    reality_sni, reality_fp, transport_type, api_token, link_template,
                    provider_kind, xray_inbound_tag, client_surface, panel_flavor,
                    ssh_user, ssh_key_path, is_active
                ) VALUES (
                    $1, $2, 'FI', '🇫🇮', $3, $4,
                    $5, NULL, '', '', '',
                    '', 0, $6, $7,
                    $8, 'firefox', $9, '', '',
                    'bare_xray', $10, 'happ', 'standard',
                    $11, $12, TRUE
                )
                """,
                row_id,
                label,
                BRAVADA2_HOST,
                port,
                ws_path,
                pbk,
                sid,
                BRAVADA2_SNI,
                transport,
                tag,
                SSH_USER,
                SSH_KEY_PATH,
            )
            print(f"  id={row_id} {label} {transport} :{port} tag={tag}")

        count = await pool.fetchval("SELECT count(*) FROM vpn_servers WHERE is_active=TRUE")
        bare = await pool.fetchval(
            "SELECT count(*) FROM vpn_servers WHERE is_active=TRUE AND provider_kind='bare_xray'"
        )
        print(f"\nTotal active={count}  bare_xray={bare}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
