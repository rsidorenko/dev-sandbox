"""Insert (or update) the vpn_servers row for the vk-tunnel LTE entry (84.201.144.227).

Takes the values printed by setup_vk_tunnel.py + the live wss domain as env:
  VKT_INBOUND_ID   the created WS inbound id (INBOUND_ID from setup)
  VKT_PANEL_URL    panel URL incl. webBasePath (PANEL_URL from setup)
  VKT_PANEL_PASS   panel password (for encrypted_password)
  VKT_WS_DOMAIN    the CURRENT vk-tunnel wss domain (from
                   /var/lib/vk-tunnel/current-domain on the server) — stored in
                   tls_sni, which the bot uses as the ws host in vless:// links.
  VKT_WS_PATH      ws path (default /vkt/)
  VKT_LABEL        bot-facing label (default "Россия 📶 VK")

The row is keyed by server_host=84.201.144.227: if it already exists, UPDATE
(idempotent re-run, e.g. after VK rotates the domain); else INSERT with the next
free id. transport_type='ws', security handled by vk-tunnel at the VK edge.

Runs in the production container with DATABASE_URL + FIELD_ENCRYPTION_KEY.
"""

import asyncio
import os
import sys

VKT_HOST = "84.201.144.227"


async def run():
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    inbound_id = os.environ.get("VKT_INBOUND_ID", "").strip()
    panel_url = os.environ.get("VKT_PANEL_URL", "").strip()
    panel_pass = os.environ.get("VKT_PANEL_PASS", "").strip()
    ws_domain = os.environ.get("VKT_WS_DOMAIN", "").strip()
    ws_path = os.environ.get("VKT_WS_PATH", "/vkt/").strip() or "/vkt/"
    label = os.environ.get("VKT_LABEL", "Россия 📶 VK").strip() or "Россия 📶 VK"

    missing = [k for k, v in [("VKT_INBOUND_ID", inbound_id), ("VKT_PANEL_URL", panel_url),
                              ("VKT_PANEL_PASS", panel_pass), ("VKT_WS_DOMAIN", ws_domain)]
               if not v]
    if missing:
        print(f"ERROR: missing env: {missing}. Run setup_vk_tunnel first, then read "
              f"/var/lib/vk-tunnel/current-domain for VKT_WS_DOMAIN.", file=sys.stderr)
        sys.exit(1)

    import asyncpg
    from app.security.field_encryption import encrypt_field

    encrypted = encrypt_field(panel_pass)
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        existing = await pool.fetchrow(
            "SELECT id FROM vpn_servers WHERE server_host = $1", VKT_HOST)
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
                row_id, label, "RU", "🇷🇺", ws_path, ws_domain, panel_url, "bravada",
                encrypted, int(inbound_id),
            )
            print(f"Updated vpn_servers row id={row_id} ({VKT_HOST}) — new wss domain={ws_domain}")
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
                row_id, label, "RU", "🇷🇺", VKT_HOST,
                ws_path, ws_domain, panel_url, "bravada",
                encrypted, int(inbound_id),
            )
            print(f"Inserted vpn_servers row id={row_id} ({VKT_HOST}) — wss domain={ws_domain}")

        row = await pool.fetchrow(
            "SELECT id, label, server_host, server_port, ws_path, tls_sni, inbound_id, "
            "       transport_type, is_active FROM vpn_servers WHERE id = $1", row_id)
        print(f"Verified: {dict(row)}")

        count = await pool.fetchval("SELECT count(*) FROM vpn_servers WHERE is_active = TRUE")
        print(f"Total active servers: {count}")
        print("\nThe bot provisions all active users on this WS inbound at next restart "
              "(reconcile_all_users). The 📶 VK server appears in every subscription, with the "
              "VK wss domain as its host — a strict-whitelist bypass.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
