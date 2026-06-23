#!/usr/bin/env python3
"""Query vpn_servers from PostgreSQL production DB."""
import asyncio, asyncpg, os

async def main():
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'])
    rows = await pool.fetch(
        "SELECT id, label, server_host, inbound_id, "
        "COALESCE(transport_type, 'tcp') as tt, is_active "
        "FROM vpn_servers ORDER BY id"
    )
    print(f"Total: {len(rows)} rows")
    for r in rows:
        print(f"  id={r['id']} host={r['server_host']} inbound={r['inbound_id']} "
              f"tt={r['tt']} active={r['is_active']} label={r['label']}")
    active = [r for r in rows if r['is_active']]
    print(f"Active: {len(active)} / {len(rows)}")
    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
