import asyncio, asyncpg, os
async def run():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        rows = await pool.fetch(
            "SELECT id, label, server_host, server_port, transport_type, tls_sni, "
            "reality_pbk, reality_sid, reality_sni, reality_fp, ws_path, inbound_id "
            "FROM vpn_servers WHERE id IN (10, 12)")
        for r in rows:
            print(f"Server {r['id']}: {r['label']}")
            print(f"  host={r['server_host']}:{r['server_port']} transport={r['transport_type']} inbound={r['inbound_id']}")
            print(f"  tls_sni={r['tls_sni']} ws_path={r['ws_path']}")
            print(f"  reality_sni={r['reality_sni']} pbk={r['reality_pbk'][:12]}... sid={r['reality_sid']} fp={r['reality_fp']}")
            print()
    finally:
        await pool.close()
asyncio.run(run())
