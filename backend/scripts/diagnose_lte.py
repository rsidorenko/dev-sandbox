import asyncio, asyncpg, json, os, re, sys, httpx

async def run():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field
        from app.issuance.xui_vless_provider import _load_server_configs, _build_vless_link
        from app.issuance.xui_vless_provider import _get_or_create_vless_uuid

        # Get configs for LTE servers
        configs = await _load_server_configs(pool)
        lte = [c for c in configs if c.server_id in (10, 12)]

        # Get one user
        user = await pool.fetchrow("SELECT internal_user_id, vless_uuid FROM user_identities WHERE vless_uuid IS NOT NULL LIMIT 1")
        uid = user["internal_user_id"]
        uuid = user["vless_uuid"]

        print(f"User: {uid[:16]}... uuid: {uuid[:12]}...")
        print()

        for c in lte:
            link = _build_vless_link(c, uuid)
            print(f"=== Server {c.server_id}: {c.label} ===")
            print(f"  host={c.server_host}:{c.server_port} transport={c.transport_type}")
            print(f"  tls_sni={c.tls_sni} reality_sni={c.reality_sni}")
            print(f"  reality_pbk={c.reality_pbk[:12]}... sid={c.reality_sid} fp={c.reality_fp}")
            print(f"  panel={c.panel_url} inbound_id={c.inbound_id}")
            print(f"  VLESS link: {link}")
            print()

            # Now check panel inbound Reality settings
            pw = await pool.fetchval(
                "SELECT COALESCE(encrypted_password, '') FROM vpn_servers WHERE id = $1", c.server_id)
            password = decrypt_field(pw) if pw else ""
            if not password:
                password = await pool.fetchval("SELECT panel_password FROM vpn_servers WHERE id = $1", c.server_id)

            panel = c.panel_url.rstrip("/")
            async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=10) as client:
                page = await client.get(f"{panel}/")
                csrf = None
                m = re.search(r'csrf-token" content="([^"]+)"', page.text)
                if m: csrf = m.group(1)
                h = {"Content-Type": "application/json"}
                if csrf: h["X-CSRF-Token"] = csrf
                login = await client.post(f"{panel}/login",
                    json={"username": c.panel_username, "password": password}, headers=h)
                if login.status_code != 200:
                    print(f"  Panel login FAILED")
                    continue

                resp = await client.get(f"{panel}/panel/api/inbounds/get/{c.inbound_id}", headers=h)
                if resp.status_code != 200:
                    print(f"  Get inbound FAILED: {resp.status_code}")
                    continue
                ib = resp.json().get("obj", {})
                stream = ib.get("streamSettings", {})
                net = stream.get("network", "?")
                sec = stream.get("security", "?")
                print(f"  Panel inbound: network={net} security={sec}")

                if "realitySettings" in stream:
                    rs = stream["realitySettings"]
                    print(f"  Reality settings:")
                    print(f"    dest={rs.get('dest','?')[:60]}")
                    print(f"    serverNames={rs.get('serverNames',[])}")
                    print(f"    shortIds={rs.get('shortIds',[])}")
                    print(f"    privateKey={str(rs.get('privateKey',''))[:12]}...")
                    print(f"    publicKey={str(rs.get('publicKey',''))[:12]}...")

                sett = json.loads(ib.get("settings", "{}"))
                clients = sett.get("clients", [])
                if clients:
                    # Find our user
                    for cl in clients:
                        if cl.get("id", "").startswith(uuid[:8]):
                            print(f"  Our client: email={cl.get('email')} flow=\"{cl.get('flow','')}\" uuid={cl.get('id','')[:12]}...")
                            break
                    else:
                        print(f"  WARNING: our UUID not found in panel! First client: {clients[0].get('email')} uuid={clients[0].get('id','')[:12]}...")
                print()
    finally:
        await pool.close()

asyncio.run(run())
