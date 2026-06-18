"""LTE-fleet prep: repoint a vpn_servers row's host/URL + dump foreign Reality keys.

Runs in the production container (DATABASE_URL). Two jobs in one run:

1. REPOINT (optional): UPDATE vpn_servers SET server_host, panel_url WHERE id=REPOINT_ID.
   Preserves encrypted_password (an IP change must NOT re-supply the panel password).
   This is the documented Yandex-IP-change operation (provider cache self-heals ~10 min,
   no restart). Idempotent. Prints before/after. Used to repoint id=10 (bgg LTE) to its
   new Yandex IP after a carrier-IP swap.

2. DUMP (read-only): for each host in DUMP_HOSTS, print the tcp-transport row's Reality
   publicKey/shortId/serverName/port/inbound_id — the values a new LTE entry's
   relay-to-<city> outbound needs. Used to fetch Helsinki/LA Reality params for the
   lhh/lla LTE entries.

Env:
  REPOINT_ID     row id to repoint (default 10 = bgg LTE). Set blank to skip repoint.
  REPOINT_HOST   new server_host
  REPOINT_URL    new panel_url
  DUMP_HOSTS     comma-separated hosts to dump (default Helsinki,LA)
"""

import asyncio
import os
import sys


async def run():
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        repoint_id = os.environ.get("REPOINT_ID", "10").strip()
        repoint_host = os.environ.get("REPOINT_HOST", "").strip()
        repoint_url = os.environ.get("REPOINT_URL", "").strip()
        if repoint_id and repoint_host:
            before = await conn.fetchrow(
                "SELECT id, label, server_host, panel_url, panel_username, inbound_id, "
                "reality_pbk, reality_sni FROM vpn_servers WHERE id=$1", int(repoint_id))
            print(f"BEFORE id={repoint_id}: {dict(before) if before else '(no row)'}")
            await conn.execute(
                "UPDATE vpn_servers SET server_host=$1, panel_url=$2 WHERE id=$3",
                repoint_host, repoint_url, int(repoint_id))
            after = await conn.fetchrow(
                "SELECT id, label, server_host, panel_url, panel_username FROM vpn_servers "
                "WHERE id=$1", int(repoint_id))
            print(f"AFTER  id={repoint_id}: {dict(after) if after else '(no row)'}")
        else:
            print("REPOINT skipped (REPOINT_ID/REPOINT_HOST not set)")

        dump_hosts = [h.strip() for h in os.environ.get(
            "DUMP_HOSTS", "77.221.159.106,216.227.169.120").split(",") if h.strip()]
        print("\n=== FOREIGN TARGET Reality keys (tcp row) ===")
        for host in dump_hosts:
            r = await conn.fetchrow(
                "SELECT id, label, server_host, server_port, inbound_id, transport_type, "
                "reality_pbk, reality_sid, reality_sni FROM vpn_servers "
                "WHERE server_host=$1 AND transport_type='tcp' ORDER BY id LIMIT 1", host)
            if r:
                d = dict(r)
                print(f"HOST={host}")
                print(f"  RELAY_HOST={d['server_host']}")
                print(f"  RELAY_PORT={d['server_port']}")
                print(f"  RELAY_INBOUND_ID={d['inbound_id']}")
                print(f"  RELAY_PBK={d['reality_pbk']}")
                print(f"  RELAY_SID={d['reality_sid']}")
                print(f"  RELAY_SNI={d['reality_sni']}")
            else:
                print(f"HOST={host}  NO tcp ROW")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
