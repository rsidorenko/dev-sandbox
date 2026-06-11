"""Add the LTE relay UUID as a client on Frankfurt inbound 2 (8443 xhttp/reality).

Frankfurt inbound 2 requires client auth — the relay outbound's UUID must be
registered here or the relay connection is rejected.

Runs in production container with FIELD_ENCRYPTION_KEY + DATABASE_URL.
"""

import asyncio
import json
import os
import re
import sys

import httpx

RELAY_UUID = "e6c57699-bf3e-494a-8eea-a1c3a86b4ec6"
RELAY_EMAIL = "relay-lte-to-frankfurt"
FRANKFURT_SERVER_ID = 5  # Frankfurt 3.0, uses inbound 2 (8443 xhttp)
FRANKFURT_INBOUND_ID = 2


async def _login(client, panel_url, username, password):
    page = await client.get(f"{panel_url}/")
    csrf = None
    if page.status_code == 200:
        m = re.search(r'csrf-token" content="([^"]+)"', page.text)
        if m:
            csrf = m.group(1)
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    login = await client.post(f"{panel_url}/login",
        json={"username": username, "password": password}, headers=headers)
    if login.status_code != 200 or not login.json().get("success"):
        raise RuntimeError(f"Login failed: {login.status_code}")
    return headers


async def run():
    import asyncpg
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field
        row = await pool.fetchrow(
            "SELECT panel_url, panel_username, panel_password, COALESCE(encrypted_password,'') AS ep "
            "FROM vpn_servers WHERE id = $1", FRANKFURT_SERVER_ID)
        pw = decrypt_field(row["ep"]) if row["ep"] else row["panel_password"]
        panel = row["panel_url"].rstrip("/")

        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=20) as client:
            headers = await _login(client, panel, row["panel_username"], pw)
            print("Frankfurt login: OK")

            # Get inbound 2
            resp = await client.get(f"{panel}/panel/api/inbounds/get/{FRANKFURT_INBOUND_ID}", headers=headers)
            ib = resp.json().get("obj", {})
            settings = ib.get("settings", "{}")
            if isinstance(settings, str):
                settings = json.loads(settings)
            clients = settings.get("clients", [])

            # Check if relay UUID already present
            existing = [c for c in clients if c.get("id") == RELAY_UUID]
            if existing:
                print(f"Relay UUID already present on inbound {FRANKFURT_INBOUND_ID}: {existing[0].get('email')}")
                return

            # Add relay client
            new_client = {
                "id": RELAY_UUID,
                "email": RELAY_EMAIL,
                "enable": True,
                "expiryTime": 0,
                "limitIp": 0,
                "subId": "",
                "tgId": "",
                "reset": 0,
                "flow": "",  # NO xtls-rprx-vision on relay client
            }
            clients.append(new_client)
            settings["clients"] = clients

            # Read-modify-write (full update)
            stream = ib.get("streamSettings", {})
            sniffing = ib.get("sniffing", {})
            update_body = {
                "up": ib.get("up", 0), "down": ib.get("down", 0), "total": ib.get("total", 0),
                "remark": ib.get("remark", ""), "enable": ib.get("enable", True),
                "expiryTime": ib.get("expiryTime", 0), "listen": ib.get("listen", ""),
                "port": ib.get("port", ""), "protocol": ib.get("protocol", "vless"),
                "settings": json.dumps(settings),
                "streamSettings": json.dumps(stream) if isinstance(stream, dict) else stream,
                "sniffing": json.dumps(sniffing) if isinstance(sniffing, dict) else sniffing,
            }
            resp = await client.post(f"{panel}/panel/api/inbounds/update/{FRANKFURT_INBOUND_ID}",
                                     headers=headers, json=update_body)
            ok = resp.status_code == 200 and resp.json().get("success", False)
            print(f"Add relay client to inbound {FRANKFURT_INBOUND_ID}: {resp.status_code} success={ok}")

            # Verify
            resp = await client.get(f"{panel}/panel/api/inbounds/get/{FRANKFURT_INBOUND_ID}", headers=headers)
            ib2 = resp.json().get("obj", {})
            s2 = json.loads(ib2.get("settings", "{}"))
            relay_in = [c for c in s2.get("clients", []) if c.get("id") == RELAY_UUID]
            print(f"Verified: relay client present = {bool(relay_in)}, total clients = {len(s2.get('clients', []))}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
