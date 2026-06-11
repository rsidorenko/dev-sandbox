"""Debug LTE inbound 7 client add — test addClient + update, report errors."""
import asyncio, os, re, json
import httpx

async def run():
    import asyncpg
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field
        row = await pool.fetchrow(
            "SELECT panel_url, panel_username, COALESCE(encrypted_password,'') AS ep, inbound_id "
            "FROM vpn_servers WHERE id = 10")
        pw = decrypt_field(row["ep"]) if row["ep"] else ""
        panel = row["panel_url"].rstrip("/")
        ib_id = row["inbound_id"]
    finally:
        await pool.close()

    print(f"Panel: {panel} inbound_id={ib_id}")
    async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=20) as c:
        # login
        page = await c.get(f"{panel}/")
        csrf = None
        m = re.search(r'csrf-token" content="([^"]+)"', page.text)
        if m: csrf = m.group(1)
        h = {"Content-Type": "application/json"}
        if csrf: h["X-CSRF-Token"] = csrf
        login = await c.post(f"{panel}/login", json={"username": row["panel_username"], "password": pw}, headers=h)
        print(f"login: {login.status_code} {login.json().get('success')}")

        # GET inbound
        resp = await c.get(f"{panel}/panel/api/inbounds/get/{ib_id}", headers=h)
        print(f"\nGET inbound {ib_id}: {resp.status_code}")
        if resp.status_code == 200:
            ib = resp.json().get("obj", {})
            print(f"  port={ib.get('port')} protocol={ib.get('protocol')} enable={ib.get('enable')}")
            s = ib.get("settings", "{}")
            if isinstance(s, str): s = json.loads(s)
            print(f"  clients: {len(s.get('clients', []))}")
            print(f"  streamSettings type: {type(ib.get('streamSettings')).__name__}")
            print(f"  sniffing type: {type(ib.get('sniffing')).__name__}")

        # Test addClient
        test_uuid = "00000000-0000-0000-0000-000000000001"
        test_client = {"id": test_uuid, "email": "test-debug-user", "enable": True,
                       "expiryTime": 0, "limitIp": 0, "subId": "", "tgId": "", "reset": 0, "flow": ""}
        print("\n--- addClient ---")
        resp = await c.post(f"{panel}/panel/api/inbounds/addClient",
                            headers=h, json={"id": ib_id, "settings": json.dumps({"clients": [test_client]})})
        print(f"addClient: {resp.status_code} {resp.text[:200]}")

        # If addClient failed, try update (read-modify-write)
        if resp.status_code != 200 or not resp.json().get("success"):
            print("\n--- update (read-modify-write) ---")
            resp = await c.get(f"{panel}/panel/api/inbounds/get/{ib_id}", headers=h)
            ib = resp.json().get("obj", {})
            s = ib.get("settings", "{}")
            if isinstance(s, str): s = json.loads(s)
            s.setdefault("clients", []).append(test_client)
            stream = ib.get("streamSettings", {})
            sniffing = ib.get("sniffing", {})
            body = {
                "up": ib.get("up",0), "down": ib.get("down",0), "total": ib.get("total",0),
                "remark": ib.get("remark",""), "enable": ib.get("enable",True),
                "expiryTime": ib.get("expiryTime",0), "listen": ib.get("listen",""),
                "port": ib.get("port",""), "protocol": ib.get("protocol","vless"),
                "settings": json.dumps(s),
                "streamSettings": json.dumps(stream) if isinstance(stream, dict) else stream,
                "sniffing": json.dumps(sniffing) if isinstance(sniffing, dict) else sniffing,
            }
            resp = await c.post(f"{panel}/panel/api/inbounds/update/{ib_id}", headers=h, json=body)
            print(f"update: {resp.status_code} {resp.text[:300]}")

        # Cleanup test client
        resp = await c.post(f"{panel}/panel/api/inbounds/{ib_id}/delClient/{test_uuid}", headers=h)
        print(f"\ncleanup delClient: {resp.status_code} {resp.text[:100]}")

asyncio.run(run())
