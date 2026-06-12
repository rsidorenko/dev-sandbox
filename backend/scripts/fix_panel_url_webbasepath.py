#!/usr/bin/env python3
"""Fix panel_url for servers with webBasePath.
Usage: python3 fix_panel_url_webbasepath.py <fra_new_url> <la_new_url>
Example: python3 fix_panel_url_webbasepath.py https://77.110.100.210:2053/abc123 https://216.227.169.120:2053/def456
"""
import asyncio, os, sys
import asyncpg


async def run():
    if len(sys.argv) < 3:
        print("Usage: fix_panel_url_webbasepath.py <fra_new_url> <la_new_url>")
        sys.exit(1)

    fra_new = sys.argv[1]
    la_new = sys.argv[2]

    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        # Update Frankfurt
        r1 = await pool.execute(
            "UPDATE vpn_servers SET panel_url = $1 WHERE server_host = '77.110.100.210' AND is_active = TRUE",
            fra_new,
        )
        print(f"Frankfurt update: {r1}")

        # Update LA
        r2 = await pool.execute(
            "UPDATE vpn_servers SET panel_url = $1 WHERE server_host = '216.227.169.120' AND is_active = TRUE",
            la_new,
        )
        print(f"LA update: {r2}")

        # Verify
        rows = await pool.fetch(
            "SELECT id, label, panel_url FROM vpn_servers "
            "WHERE server_host IN ('77.110.100.210','216.227.169.120') ORDER BY id"
        )
        print(f"\nUpdated rows ({len(rows)}):")
        for x in rows:
            print(f"  id={x['id']} label={x['label']} url={x['panel_url']}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
