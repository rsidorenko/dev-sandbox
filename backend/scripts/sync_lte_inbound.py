"""Sync LTE inbound 7 clients to match user_identities.vless_uuid (bypasses addClient bug).

Uses the working read-modify-write update API. Idempotent.
"""
import asyncio, os, re, json
import httpx

async def run():
    import asyncpg
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field
        # Get all active users with vless_uuid
        users = await pool.fetch(
            "SELECT internal_user_id, vless_uuid FROM user_identities WHERE vless_uuid IS NOT NULL")
        print(f"Users with vless_uuid: {len(users)}")

        row = await pool.fetchrow(
            "SELECT panel_url, panel_username, COALESCE(encrypted_password,'') AS ep, inbound_id "
            "FROM vpn_servers WHERE id = 10")
        pw = decrypt_field(row["ep"]) if row["ep"] else ""
        panel = row["panel_url"].rstrip("/")
        ib_id = row["inbound_id"]

        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=20) as c:
            page = await c.get(f"{panel}/")
            csrf = None
            m = re.search(r'csrf-token" content="([^"]+)"', page.text)
            if m: csrf = m.group(1)
            h = {"Content-Type": "application/json"}
            if csrf: h["X-CSRF-Token"] = csrf
            login = await c.post(f"{panel}/login", json={"username": row["panel_username"], "password": pw}, headers=h)
            print(f"login: {login.status_code} {login.json().get('success')}")

            # Get current inbound
            resp = await c.get(f"{panel}/panel/api/inbounds/get/{ib_id}", headers=h)
            inbound = resp.json()["obj"]
            settings = inbound.get("settings", {})
            if isinstance(settings, str): settings = json.loads(settings)
            current = settings.get("clients", [])
            current_uuids = {c.get("id") for c in current}
            db_uuids = {u["vless_uuid"] for u in users}
            print(f"inbound clients: {len(current_uuids)}, DB uuids: {len(db_uuids)}")
            print(f"inbound but not DB (stale): {len(current_uuids - db_uuids)}")
            print(f"DB but not inbound (missing): {len(db_uuids - current_uuids)}")

            # Build fresh client list from DB (drop stale, add missing)
            new_clients = []
            for u in users:
                uid = u["internal_user_id"]
                uuid = u["vless_uuid"]
                new_clients.append({
                    "id": uuid, "email": f"user-{uid[:16]}", "enable": True,
                    "expiryTime": 0, "flow": "", "limitIp": 5, "totalGB": 0,
                    "tgId": "", "subId": "", "reset": 0,
                })
            settings["clients"] = new_clients
            print(f"writing {len(new_clients)} clients to inbound {ib_id}...")

            payload = {
                "id": inbound["id"], "settings": json.dumps(settings),
                "streamSettings": json.dumps(inbound["streamSettings"]) if isinstance(inbound.get("streamSettings"), dict) else inbound.get("streamSettings", ""),
                "sniffing": json.dumps(inbound["sniffing"]) if isinstance(inbound.get("sniffing"), dict) else inbound.get("sniffing", ""),
                "protocol": inbound["protocol"], "port": inbound["port"],
                "listen": inbound.get("listen", ""), "tag": inbound.get("tag", ""),
                "remark": inbound.get("remark", ""), "enable": inbound.get("enable", True),
                "expiryTime": inbound.get("expiryTime", 0), "total": inbound.get("total", 0),
                "up": inbound.get("up", 0), "down": inbound.get("down", 0),
            }
            resp = await c.post(f"{panel}/panel/api/inbounds/update/{ib_id}", headers=h, json=payload)
            ok = resp.status_code == 200 and resp.json().get("success")
            print(f"update: {resp.status_code} success={ok}")

            # Verify
            resp = await c.get(f"{panel}/panel/api/inbounds/get/{ib_id}", headers=h)
            ib2 = resp.json()["obj"]
            s2 = ib2.get("settings", {})
            if isinstance(s2, str): s2 = json.loads(s2)
            print(f"verified: inbound now has {len(s2.get('clients', []))} clients")
    finally:
        await pool.close()

asyncio.run(run())
