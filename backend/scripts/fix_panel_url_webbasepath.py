#!/usr/bin/env python3
"""Fix panel_url for servers with webBasePath: SSH to each 3x-ui server,
read the webBasePath from its SQLite DB, and update the PostgreSQL vpn_servers table."""
import asyncio, os, sys
import asyncpg

# webBasePath values discovered via SSH to each server's 3x-ui SQLite DB
# These were obtained by running:
#   ssh root@77.110.100.210 "python3 -c \"import sqlite3; db=sqlite3.connect('/etc/x-ui/x-ui.db'); c=db.cursor(); c.execute('SELECT value FROM settings WHERE key=chr(119)+chr(101)+chr(98)+chr(66)+chr(97)+chr(115)+chr(101)+chr(80)+chr(97)+chr(116)+chr(104)'); print(c.fetchone()[0])\""
#
# Frankfurt: <to be filled>
# LA: <to be filled>

# Map server_host -> correct panel_url (with webBasePath)
FIXES: dict[str, str] = {
    # Frankfurt (77.110.100.210) — all 3 inbounds share one panel
    "77.110.100.210": None,  # placeholder — will be set after discovery
    # LA (216.227.169.120) — 2 inbounds share one panel
    "216.227.169.120": None,  # placeholder — will be set after discovery
}


async def run():
    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        # Show current state
        rows = await pool.fetch(
            "SELECT id, label, server_host, panel_url FROM vpn_servers "
            "WHERE server_host = ANY($1::text[]) AND is_active = TRUE ORDER BY id",
            list(FIXES.keys()),
        )
        print(f"Servers to fix: {len(rows)}")
        for r in rows:
            print(f"  id={r['id']} label={r['label']} host={r['server_host']} url={r['panel_url']}")

        if not any(FIXES.values()):
            print("\nNo webBasePath values configured yet. Discovery needed first.")
            print("Run via deploy workflow with check_frankfurt_logs=true to SSH into servers.")
            return

        # Apply fixes
        for host, new_base in FIXES.items():
            if not new_base:
                continue
            # Get current rows for this host
            host_rows = [r for r in rows if r["server_host"] == host]
            if not host_rows:
                print(f"  No rows found for {host}")
                continue
            # All inbounds on same host share same panel URL
            old_url = host_rows[0]["panel_url"].rstrip("/")
            new_url = new_base.rstrip("/")
            if old_url == new_url:
                print(f"  {host}: already correct ({new_url})")
                continue
            print(f"  {host}: {old_url} -> {new_url}")
            result = await pool.execute(
                "UPDATE vpn_servers SET panel_url = $1 WHERE server_host = $2 AND is_active = TRUE",
                new_url, host,
            )
            print(f"    Updated: {result}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
