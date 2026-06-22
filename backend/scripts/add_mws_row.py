"""Insert (or update) the vpn_servers row for the MWS (МТС Web Services) CDN-front
entry.

The client-facing host is the MWS edge hostname (topXXXXXXXX.mwscdn.ru, AS8359 МТС
— hopefully whitelisted during ЧС). The PANEL that manages clients on the origin
inbound is the Frankfurt 3x-ui (where setup_mws_origin.py created the WS inbound),
so panel creds are COPIED from an existing Frankfurt row (same panel, different
inbound_id) — no plaintext password needed here.

Bot link building: transport_type='ws' -> _build_vless_link uses tls_sni as the ws
host, so storing the MWS hostname in tls_sni makes /sub/ emit
vless://...@<mws-host>:443?type=ws&security=tls&path=/mws&host=<mws-host>&sni=<mws-host>.

Keyed by server_host=<MWS_HOSTNAME>: idempotent (UPDATE if exists, else INSERT next
free id). Runs in the prod container with DATABASE_URL.
"""

import asyncio
import os
import sys


async def run() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    hostname = os.environ.get("MWS_HOSTNAME", "").strip()
    inbound_id = os.environ.get("MWS_INBOUND_ID", "").strip()
    ws_path = os.environ.get("MWS_WS_PATH", "/mws").strip() or "/mws"
    label = os.environ.get("MWS_LABEL", "🇷🇺 MWS МТС").strip() or "🇷🇺 MWS МТС"
    origin_panel_host = os.environ.get("MWS_ORIGIN_PANEL_HOST", "77.110.100.210").strip()

    if not (hostname and inbound_id):
        print(
            "ERROR: MWS_HOSTNAME and MWS_INBOUND_ID required "
            "(run setup_mws_origin.py first, copy INBOUND_ID).",
            file=sys.stderr,
        )
        sys.exit(1)

    import asyncpg

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        # Copy panel creds from the existing Frankfurt origin row (same 3x-ui panel).
        donor = await pool.fetchrow(
            "SELECT panel_url, panel_username, encrypted_password, country_code, country_flag "
            "FROM vpn_servers WHERE server_host = $1 LIMIT 1",
            origin_panel_host,
        )
        if not donor:
            print(
                f"ERROR: no existing vpn_servers row for origin panel host "
                f"{origin_panel_host} to copy creds from.",
                file=sys.stderr,
            )
            sys.exit(1)
        panel_url = donor["panel_url"]
        panel_username = donor["panel_username"]
        encrypted = donor["encrypted_password"]
        country_code = donor["country_code"] or "RU"
        country_flag = donor["country_flag"] or "🇷🇺"

        existing = await pool.fetchrow(
            "SELECT id FROM vpn_servers WHERE server_host = $1", hostname
        )
        if existing:
            row_id = existing["id"]
            await pool.execute(
                """
                UPDATE vpn_servers SET
                    label=$2, country_code=$3, country_flag=$4, server_port=443,
                    ws_path=$5, tls_sni=$6, panel_url=$7, panel_username=$8,
                    panel_password='', encrypted_password=$9, inbound_id=$10,
                    reality_pbk='', reality_sid='', reality_sni='', reality_fp='chrome',
                    transport_type='ws', is_active=TRUE
                WHERE id=$1
                """,
                row_id, label, country_code, country_flag, ws_path, hostname,
                panel_url, panel_username, encrypted, int(inbound_id),
            )
            print(f"Updated vpn_servers row id={row_id} ({hostname}) — MWS CDN ws entry")
        else:
            row_id = await pool.fetchval("SELECT COALESCE(MAX(id), 0) + 1 FROM vpn_servers")
            await pool.execute(
                """
                INSERT INTO vpn_servers (
                    id, label, country_code, country_flag, server_host, server_port,
                    ws_path, tls_sni, panel_url, panel_username, panel_password,
                    encrypted_password, inbound_id, reality_pbk, reality_sid,
                    reality_sni, reality_fp, transport_type, is_active
                ) VALUES (
                    $1, $2, $3, $4, $5, 443,
                    $6, $7, $8, $9, '',
                    $10, $11, '', '', '',
                    'chrome', 'ws', TRUE
                )
                """,
                row_id, label, country_code, country_flag, hostname,
                ws_path, hostname, panel_url, panel_username,
                encrypted, int(inbound_id),
            )
            print(f"Inserted vpn_servers row id={row_id} ({hostname}) — MWS CDN ws entry")

        row = await pool.fetchrow(
            "SELECT id, label, server_host, server_port, ws_path, tls_sni, inbound_id, "
            "       transport_type, is_active FROM vpn_servers WHERE id = $1",
            row_id,
        )
        print(f"Verified: {dict(row)}")

        count = await pool.fetchval("SELECT count(*) FROM vpn_servers WHERE is_active = TRUE")
        print(f"Total active servers: {count}")
        print(
            "\nThe bot provisions all active users on this WS inbound at next "
            "reprovision_active run (or reconcile). The MWS entry then appears in every "
            "/sub/ with the MWS edge hostname as its ws host."
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
