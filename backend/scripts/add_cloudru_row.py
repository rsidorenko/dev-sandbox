"""Insert the vpn_servers row for the Cloud.ru RU-relay entry (bare-xray, Happ).

Cloud.ru (85.208.87.187) is the clean-AS RU entry of the 2-hop relay
(Cloud.ru -> Frankfurt egress) that makes VPN work in the Happ client, which the
3x-ui fleet cannot serve (3x-ui realitySettings breaks xtls-rprx-vision for
Xray-core/Happ). Cloud.ru runs BARE xray (no 3x-ui panel) — its Reality inbound,
routing and bravada camo are configured out-of-band; the bot only needs the row to
build the /sub/ link for Happ users.

This row is provider_kind='bare_xray':
  * the VLESS provider SKIPS panel CRUD for it (no panel_url/inbound_id) and
    builds its vless:// link straight from the row (tcp branch of _build_vless_link,
    reality_fp='firefox' — required for Happ on a clean AS);
  * user UUIDs on it are managed LIVE via xray's gRPC API (XrayApiClient:
    AddUser/RemoveUser on subscribe/expire + reconcile), NOT by panel calls.
    The xray inbound tag for this row is set by add_cloudru_xhttp_row.py.

Runs in the production container with DATABASE_URL. Idempotent: re-running
re-inserts the same row.
"""

import asyncio
import os
import sys

# Cloud.ru VM (Sbercloud AS208677, Moscow). bare-xray Reality :443, bravada camo
# cloudru.bravada-connect.online (Let's Encrypt), -> Frankfurt relay.
CLOUDRU_HOST = "85.208.87.187"
ROW_ID = 26  # next free after the NL whitelist-bypass rows (22-25); collision-checked below

# Reality params of Cloud.ru's :443 inbound (PUBLIC values — safe to commit; they are
# the same values every Happ user's /sub/ link carries).
CLOUDRU_PBK = "zHzFXnxH13f2lUXJZ3_Lqi3K9O6auoFjS84G57JLQD4"
CLOUDRU_SID = "a4084380"
CLOUDRU_SNI = "cloudru.bravada-connect.online"


async def run() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        existing = await pool.fetchrow("SELECT id, label, server_host FROM vpn_servers WHERE id = $1", ROW_ID)
        if existing and existing["server_host"] != CLOUDRU_HOST:
            print(
                f"ERROR: id={ROW_ID} already used by {existing['label']!r} ({existing['server_host']}). "
                f"Pick a different id.",
                file=sys.stderr,
            )
            sys.exit(1)

        await pool.execute("DELETE FROM vpn_servers WHERE id = $1", ROW_ID)
        await pool.execute(
            """
            INSERT INTO vpn_servers (
                id, label, country_code, country_flag, server_host, server_port,
                ws_path, tls_sni, panel_url, panel_username, panel_password,
                encrypted_password, inbound_id, reality_pbk, reality_sid,
                reality_sni, reality_fp, transport_type, api_token, link_template,
                provider_kind, client_surface, is_active
            ) VALUES (
                $1, $2, $3, $4, $5, 443,
                '/ws', NULL, '', '', '',
                '', 0, $6, $7,
                $8, 'firefox', 'tcp', '', '',
                'bare_xray', 'happ', TRUE
            )
            """,
            ROW_ID,
            "Франкфурт 1.0(Happ)",  # label — users exit via Frankfurt (DE), not the RU entry
            "DE",  # country_code
            "🇩🇪",  # country_flag
            CLOUDRU_HOST,  # server_host
            CLOUDRU_PBK,  # reality_pbk
            CLOUDRU_SID,  # reality_sid
            CLOUDRU_SNI,  # reality_sni
        )
        print(f"Inserted vpn_servers row id={ROW_ID} ({CLOUDRU_HOST}) provider_kind=bare_xray")

        row = await pool.fetchrow(
            "SELECT id, label, server_host, server_port, transport_type, reality_fp, "
            "       reality_sni, provider_kind, is_active "
            "FROM vpn_servers WHERE id = $1",
            ROW_ID,
        )
        print(f"Verified: {dict(row)}")

        count = await pool.fetchval("SELECT count(*) FROM vpn_servers WHERE is_active = TRUE")
        bare = await pool.fetchval(
            "SELECT count(*) FROM vpn_servers WHERE is_active = TRUE AND provider_kind = 'bare_xray'"
        )
        print(f"Total active servers: {count} (bare_xray: {bare})")
        print(
            "\nThe provider builds the Happ /sub/ link from this row (no panel ops). "
            "Run scripts/mirror_cloudru_clients.py to seed active users' UUIDs on Cloud.ru."
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
