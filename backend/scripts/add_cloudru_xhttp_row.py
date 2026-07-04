"""Insert the Cloud.ru xhttp inbound row (id=27) + set xray inbound tags.

Companion to add_cloudru_row.py (which created id=26, the tcp/reality entry). With the
gRPC-API upgrade, Cloud.ru runs TWO inbounds — `cloudru-tcp` (:443) and `cloudru-xhttp`
(:8443) — both managed live via xray's HandlerService.AlterInbound (AddUser/RemoveUser).
This script:
  * inserts id=27 (provider_kind='bare_xray', transport_type='xhttp', :8443,
    path /bravada-xhttp, same bravada-camo reality as id=26, fp=firefox, xray_inbound_tag
    'cloudru-xhttp');
  * sets id=26.xray_inbound_tag = 'cloudru-tcp' (the existing tcp inbound's tag on the box).

Runs in the production container with DATABASE_URL. Idempotent.
"""

import asyncio
import os
import sys

CLOUDRU_HOST = "85.208.87.187"
ROW_ID = 27  # next free after id=26 (cloudru tcp)

# Same Reality keypair + bravada camo as id=26 (PUBLIC — same values every Happ link carries).
CLOUDRU_PBK = "zHzFXnxH13f2lUXJZ3_Lqi3K9O6auoFjS84G57JLQD4"
CLOUDRU_SID = "a4084380"
CLOUDRU_SNI = "cloudru.bravada-connect.online"
XHTTP_PATH = "/bravada-xhttp"


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
                f"ERROR: id={ROW_ID} already used by {existing['label']!r} ({existing['server_host']}).",
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
                provider_kind, xray_inbound_tag, is_active
            ) VALUES (
                $1, $2, $3, $4, $5, 8443,
                $6, NULL, '', '', '',
                '', 0, $7, $8,
                $9, 'firefox', 'xhttp', '', '',
                'bare_xray', 'cloudru-xhttp', TRUE
            )
            """,
            ROW_ID,
            "Франкфурт 2.0(Happ)",  # label — users exit via Frankfurt (DE), not the RU entry
            "DE",  # country_code
            "🇩🇪",  # country_flag
            CLOUDRU_HOST,  # server_host
            XHTTP_PATH,  # ws_path (xhttp path)
            CLOUDRU_PBK,  # reality_pbk
            CLOUDRU_SID,  # reality_sid
            CLOUDRU_SNI,  # reality_sni
        )
        # Tag the existing tcp inbound row (id=26).
        await pool.execute("UPDATE vpn_servers SET xray_inbound_tag = 'cloudru-tcp' WHERE id = 26")
        print(f"Inserted id={ROW_ID} (cloudru-xhttp) + tagged id=26 (cloudru-tcp)")

        rows = await pool.fetch(
            "SELECT id, label, server_host, server_port, transport_type, provider_kind, "
            "       xray_inbound_tag, is_active "
            "FROM vpn_servers WHERE id IN (26, 27) ORDER BY id"
        )
        for r in rows:
            print(f"  {dict(r)}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
