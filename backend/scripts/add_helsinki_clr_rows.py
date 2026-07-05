"""Insert vpn_servers rows for the cloud.ru Helsinki-egress Happ relay entry.

Second bare_xray RU-relay entry (cloud.ru / Sbercloud AS208677, same clean-AS as the Frankfurt
Cloud.ru entry 85.208.87.187), egressing via Helsinki (77.221.159.106). Gives Happ users a
Helsinki-egress variant alongside Cloud.ru's Frankfurt egress (id=26/27). Same architecture:
bare xray 26.3.27, bravada-camo (clr.mediagenering.online), fp=firefox, 2-hop relay (RU entry →
Helsinki), live gRPC management (XrayApiClient over SSH + grpcurl).

Replaces the abandoned bravada2 attempt (176.123.167.72 / bravada2.bravada-connect.online — host
dead, domain NXDOMAIN). Reuses the same row ids (28 tcp, 29 xhttp); idempotent (DELETE + INSERT).

The new machine uses the SAME SSH creds as Cloud.ru (romtan + cloudru_key.pem), so ssh_user /
ssh_key_path are NULL → the bot's CLOUDRU_SSH_USER / CLOUDRU_SSH_KEY env fallback applies (exactly
like id=26/27). No per-row SSH override, no extra key mount.

Run AFTER the server setup produced the Reality keypair (pbk/sid hardcoded below from the deployed
xray on 176.108.250.237). Runs in the production container with DATABASE_URL. Idempotent.
"""

import asyncio
import os
import sys

CLR_HOST = "176.108.250.237"
CLR_SNI = "clr.mediagenering.online"
CLR_PBK = "-pCiiUtqBhNYnwH3Kj4TeybTiXJAMSAsUB_lV_3o3SU"  # reality publicKey (from xray x25519)
CLR_SID = "2ef05ac5"  # reality shortId
XHTTP_PATH = "/bravada-xhttp"


async def run() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        for row_id, label, port, transport, tag, ws_path in (
            (28, "Финляндия 1.0(Happ)", 443, "tcp", "clr-tcp", "/ws"),
            (29, "Финляндия 2.0(Happ)", 8443, "xhttp", "clr-xhttp", XHTTP_PATH),
        ):
            existing = await pool.fetchrow("SELECT server_host FROM vpn_servers WHERE id=$1", row_id)
            if existing and existing["server_host"] not in (CLR_HOST, "176.123.167.72"):
                # someone else legitimately owns this id — refuse rather than clobber
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
                    NULL, NULL, TRUE
                )
                """,
                row_id,
                label,
                CLR_HOST,
                port,
                ws_path,
                CLR_PBK,
                CLR_SID,
                CLR_SNI,
                transport,
                tag,
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
