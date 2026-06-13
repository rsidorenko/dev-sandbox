"""Insert the vpn_servers row for the RU split-routing relay (89.169.139.153).

Takes the dynamic values printed by setup_relay.py as env vars:
  RELAY_PUBKEY      this server's Reality public key (REALITY_PUBKEY from setup)
  RELAY_INBOUND_ID  the created inbound id (INBOUND_ID from setup)
  RELAY_PANEL_URL   panel URL incl. webBasePath (PANEL_URL from setup)
  RELAY_PANEL_PASS  panel password (for encrypted_password)

Runs in the production container with DATABASE_URL + FIELD_ENCRYPTION_KEY.

Once inserted with is_active=TRUE, the bot picks it up (_load_server_configs) and
reconcile_all_active_users provisions every active user's UUID on the new inbound
at next bot restart. Split routing is purely server-side in xray — no provider
code changes needed.
"""

import asyncio
import os
import sys

RELAY_HOST = "89.169.139.153"
ROW_ID = 11  # next free after 1-9 (active) and 10 (LTE); verified collision-free below


async def run():
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    pubkey = os.environ.get("RELAY_PUBKEY", "").strip()
    inbound_id = os.environ.get("RELAY_INBOUND_ID", "").strip()
    panel_url = os.environ.get("RELAY_PANEL_URL", "").strip()
    panel_pass = os.environ.get("RELAY_PANEL_PASS", "").strip()

    missing = [k for k, v in [("RELAY_PUBKEY", pubkey), ("RELAY_INBOUND_ID", inbound_id),
                              ("RELAY_PANEL_URL", panel_url), ("RELAY_PANEL_PASS", panel_pass)] if not v]
    if missing:
        print(f"ERROR: missing env: {missing}. Run setup_relay first and pass its summary values.",
              file=sys.stderr)
        sys.exit(1)

    import asyncpg
    from app.security.field_encryption import encrypt_field

    encrypted = encrypt_field(panel_pass)
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        # Verify ROW_ID is free / not already this server; avoid clobbering an unrelated row.
        existing = await pool.fetchrow("SELECT id, label, server_host FROM vpn_servers WHERE id = $1", ROW_ID)
        if existing and existing["server_host"] != RELAY_HOST:
            print(f"ERROR: id={ROW_ID} already used by {existing['label']!r} ({existing['server_host']}). "
                  f"Pick a different id.", file=sys.stderr)
            sys.exit(1)

        await pool.execute("DELETE FROM vpn_servers WHERE id = $1", ROW_ID)
        await pool.execute(
            """
            INSERT INTO vpn_servers (
                id, label, country_code, country_flag, server_host, server_port,
                ws_path, tls_sni, panel_url, panel_username, panel_password,
                encrypted_password, inbound_id, reality_pbk, reality_sid,
                reality_sni, reality_fp, transport_type, is_active
            ) VALUES (
                $1, $2, $3, $4, $5, 443,
                '/ws', NULL, $6, $7, '',
                $8, $9, $10, $11,
                $12, 'chrome', 'tcp', TRUE
            )
            """,
            ROW_ID,
            "Россия 🔄",            # label
            "RU",                    # country_code
            "🇷🇺",                    # country_flag
            RELAY_HOST,              # server_host
            panel_url,               # panel_url
            "bravada",               # panel_username
            encrypted,               # encrypted_password
            int(inbound_id),         # inbound_id
            pubkey,                  # reality_pbk
            "a1b2c3d4e5f6",          # reality_sid
            "max.ru",                # reality_sni
        )
        print(f"Inserted vpn_servers row id={ROW_ID} ({RELAY_HOST})")

        row = await pool.fetchrow(
            "SELECT id, label, server_host, server_port, inbound_id, reality_pbk, "
            "       reality_sid, reality_sni, transport_type, is_active "
            "FROM vpn_servers WHERE id = $1", ROW_ID)
        print(f"Verified: {dict(row)}")

        count = await pool.fetchval("SELECT count(*) FROM vpn_servers WHERE is_active = TRUE")
        print(f"Total active servers: {count}")
        print("\nThe bot will provision all active users on this inbound at next restart "
              "(reconcile_all_active_users). The 🇷🇺 server appears in every subscription.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
