"""Replicate the bot's EXACT _add_client_via_update payload to find the failure."""
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

    async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=20) as c:
        page = await c.get(f"{panel}/")
        csrf = None
        m = re.search(r'csrf-token" content="([^"]+)"', page.text)
        if m: csrf = m.group(1)
        h = {"Content-Type": "application/json"}
        if csrf: h["X-CSRF-Token"] = csrf
        await c.post(f"{panel}/login", json={"username": row["panel_username"], "password": pw}, headers=h)

        # addClient exactly as bot sends
        test_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        client_settings = {"id": test_uuid, "email": "precise-test", "enable": True,
                           "expiryTime": 0, "flow": "", "limitIp": 0, "totalGB": 0, "tgId": "", "subId": ""}
        print("=== addClient (bot-style) ===")
        resp = await c.post(f"{panel}/panel/api/inbounds/addClient", headers=h,
            json={"id": ib_id, "settings": f'{{"clients": [{json.dumps(client_settings, separators=(",",":"))}]}}'})
        print(f"  {resp.status_code} -> {resp.text[:150]}")

        # Now replicate _add_client_via_update EXACTLY (with tag + id)
        print("\n=== update (bot-style payload WITH tag+id) ===")
        resp = await c.get(f"{panel}/panel/api/inbounds/get/{ib_id}", headers=h)
        inbound = resp.json()["obj"]
        settings = inbound.get("settings", {})
        if isinstance(settings, str): settings = json.loads(settings)
        settings.setdefault("clients", []).append(client_settings)
        payload = {
            "id": inbound["id"],
            "settings": json.dumps(settings),
            "streamSettings": json.dumps(inbound["streamSettings"]) if isinstance(inbound.get("streamSettings"), dict) else inbound.get("streamSettings", ""),
            "sniffing": json.dumps(inbound["sniffing"]) if isinstance(inbound.get("sniffing"), dict) else inbound.get("sniffing", ""),
            "protocol": inbound["protocol"],
            "port": inbound["port"],
            "listen": inbound.get("listen", ""),
            "tag": inbound.get("tag", ""),
            "remark": inbound.get("remark", ""),
            "enable": inbound.get("enable", True),
            "expiryTime": inbound.get("expiryTime", 0),
            "total": inbound.get("total", 0),
            "up": inbound.get("up", 0),
            "down": inbound.get("down", 0),
        }
        resp = await c.post(f"{panel}/panel/api/inbounds/update/{ib_id}", headers=h, json=payload)
        print(f"  {resp.status_code} -> {resp.text[:300]}")

        # cleanup: remove test client via update
        resp = await c.get(f"{panel}/panel/api/inbounds/get/{ib_id}", headers=h)
        inbound = resp.json()["obj"]
        settings = inbound.get("settings", {})
        if isinstance(settings, str): settings = json.loads(settings)
        settings["clients"] = [cl for cl in settings.get("clients", []) if cl.get("id") != test_uuid and cl.get("email") != "precise-test"]
        payload["settings"] = json.dumps(settings)
        await c.post(f"{panel}/panel/api/inbounds/update/{ib_id}", headers=h, json=payload)
        print("\ncleanup done")

asyncio.run(run())
