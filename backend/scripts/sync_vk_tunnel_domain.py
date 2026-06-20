"""Keep vpn_servers.tls_sni in sync with the live vk-tunnel wss domain.

VK can rotate the wss:// domain when the tunnel restarts. The relay-setup
`vk_tunnel action=sync` step reads /var/lib/vk-tunnel/current-domain on the
server (written by the extractor cron) and passes it here as VKT_WS_DOMAIN; this
UPDATEs tls_sni for the vk-tunnel server so the bot's /sub/ links keep pointing
at the working domain.

Idempotent: no-op if the domain is unchanged. Runs in the production container
with DATABASE_URL.
"""

import asyncio
import os
import sys

VKT_HOST = os.environ.get("VKT_HOST", "84.201.144.227")


async def run():
    domain = os.environ.get("VKT_WS_DOMAIN", "").strip()
    if not domain:
        print("ERROR: VKT_WS_DOMAIN not set", file=sys.stderr)
        sys.exit(1)
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        row = await pool.fetchrow(
            "SELECT id, tls_sni FROM vpn_servers WHERE server_host = $1", VKT_HOST)
        if not row:
            print(f"no vpn_servers row for {VKT_HOST} — run add_vk_tunnel_row.py first",
                  file=sys.stderr)
            sys.exit(1)
        if row["tls_sni"] == domain:
            print(f"tls_sni already {domain} for id={row['id']} — no change")
            return
        await pool.execute(
            "UPDATE vpn_servers SET tls_sni = $1 WHERE server_host = $2", domain, VKT_HOST)
        print(f"updated tls_sni for id={row['id']} ({VKT_HOST}): "
              f"{row['tls_sni']!r} -> {domain!r}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
