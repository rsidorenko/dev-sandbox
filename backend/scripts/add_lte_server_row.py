"""Insert vpn_servers row id=10 for the rebuilt LTE relay server.

Runs in production container with FIELD_ENCRYPTION_KEY + DATABASE_URL.
"""

import asyncio
import os
import sys

LTE_PANEL_PASS = os.environ.get("LTE_PANEL_PASS", "")


async def run():
    import asyncpg
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        from app.security.field_encryption import encrypt_field

        if not LTE_PANEL_PASS:
            print("ERROR: LTE_PANEL_PASS env not set", file=sys.stderr)
            sys.exit(1)

        encrypted = encrypt_field(LTE_PANEL_PASS)

        # Delete existing row 10 if any (idempotent)
        await pool.execute("DELETE FROM vpn_servers WHERE id = 10")

        await pool.execute(
            """
            INSERT INTO vpn_servers (
                id, label, country_code, country_flag, server_host, server_port,
                ws_path, tls_sni, panel_url, panel_username, panel_password,
                encrypted_password, inbound_id, reality_pbk, reality_sid,
                reality_sni, reality_fp, transport_type, is_active
            ) VALUES (
                10, $1, $2, $3, $4, 443,
                '/ws', NULL, $5, $6, '',
                $7, $8, $9, $10,
                $11, 'chrome', 'tcp', TRUE
            )
            """,
            "Франкфурт 📶 LTE",        # label
            "DE",                       # country_code
            "🇩🇪",                       # country_flag
            "93.77.188.217",            # server_host
            "https://93.77.188.217:54023/Cq6xxAccNLaSEBcR0L",  # panel_url
            "bravada",                  # panel_username
            encrypted,                  # encrypted_password
            7,                          # inbound_id (created by setup_lte)
            "ZkC6W4xrWY3Thu4lcXz0VzujJFpMjLeKh2n-E3JfO3I",  # reality_pbk
            "a1b2c3d4e5f6",             # reality_sid
            "vk.com",                   # reality_sni
        )
        print("Inserted vpn_servers row id=10")

        # Verify
        row = await pool.fetchrow(
            "SELECT id, label, server_host, inbound_id, reality_pbk, transport_type, is_active "
            "FROM vpn_servers WHERE id = 10")
        print(f"Verified: {dict(row)}")

        count = await pool.fetchval("SELECT count(*) FROM vpn_servers WHERE is_active = TRUE")
        print(f"Total active servers: {count}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
