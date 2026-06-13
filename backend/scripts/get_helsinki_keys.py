"""Print Helsinki Reality params (public key, shortId, SNI, port) from the production DB.

Used to verify the HELSINKI_* constants baked into setup_relay.py match the live
Helsinki inbound before configuring the relay outbound. These are server *public*
keys (safe to print) — they are the same values embedded in every user's
Helsinki VLESS link via _build_vless_link.

Runs in the production container with DATABASE_URL.
"""

import asyncio
import os
import sys


HELSINKI_HOST = "77.221.159.106"


async def run():
    import asyncpg

    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        # Helsinki has 3 rows (id 1/2/3) on the same host; the :443 tcp+reality
        # entry is the one the relay outbound targets. Pull the tcp row.
        rows = await conn.fetch(
            "SELECT id, label, server_host, server_port, inbound_id, transport_type, "
            "       reality_pbk, reality_sid, reality_sni "
            "FROM vpn_servers WHERE server_host = $1 ORDER BY id",
            HELSINKI_HOST,
        )
        if not rows:
            print(f"ERROR: no vpn_servers rows for {HELSINKI_HOST}", file=sys.stderr)
            sys.exit(1)

        print(f"Helsinki server rows ({HELSINKI_HOST}):")
        for r in rows:
            print(f"  id={r['id']} label={r['label']!r} port={r['server_port']} "
                  f"inbound={r['inbound_id']} transport={r['transport_type']}")
            print(f"    reality_pbk={r['reality_pbk']}")
            print(f"    reality_sid={r['reality_sid']}")
            print(f"    reality_sni={r['reality_sni']}")

        # Prefer the tcp transport row (the :443 VLESS+Reality inbound the relay hits)
        tcp = [r for r in rows if r["transport_type"] == "tcp"] or rows
        r = tcp[0]
        print("\n=== RELAY OUTBOUND TARGET (use these in setup_relay) ===")
        print(f"HELSINKI_HOST={r['server_host']}")
        print(f"HELSINKI_PORT={r['server_port']}")
        print(f"HELSINKI_PBK={r['reality_pbk']}")
        print(f"HELSINKI_SID={r['reality_sid']}")
        print(f"HELSINKI_SNI={r['reality_sni']}")
        print(f"HELSINKI_INBOUND_ID={r['inbound_id']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
