"""Clean up LA panel inbound 3 (the 2.0 config) after DB deletion.

DB row 8 (LA 2.0) is deleted. The panel inbound 3 still has clients.
This script logs into the LA panel and clears clients from inbound 3.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx


async def run():
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field

        # LA panel is shared by servers 7 and 9 (survivors). Get creds from server 7.
        row = await pool.fetchrow(
            "SELECT id, label, panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password, '') AS ep "
            "FROM vpn_servers WHERE id = 7"
        )
        if not row:
            print("ERROR: server 7 (LA 1.0) not found in DB")
            sys.exit(1)

        panel_url = row["panel_url"].rstrip("/")
        pw = decrypt_field(row["ep"]) if row["ep"] else row["panel_password"]
        username = row["panel_username"]

        print(f"LA Panel: {panel_url}")
        print(f"User: {username}")

        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=20) as client:
            # Try login - LA panel may use form data instead of JSON
            page = await client.get(f"{panel_url}/")

            # Try JSON login first
            headers_json = {"Content-Type": "application/json"}
            import re
            csrf = None
            if page.status_code == 200:
                m = re.search(r'csrf-token" content="([^"]+)"', page.text)
                if m:
                    csrf = m.group(1)
            if csrf:
                headers_json["X-CSRF-Token"] = csrf

            login = await client.post(f"{panel_url}/login",
                json={"username": username, "password": pw}, headers=headers_json)
            print(f"JSON login: {login.status_code}")

            if login.status_code != 200 or not login.json().get("success"):
                # Try form-data login
                headers_form = {}
                if csrf:
                    headers_form["X-CSRF-Token"] = csrf
                login2 = await client.post(f"{panel_url}/login",
                    data={"username": username, "password": pw}, headers=headers_form)
                print(f"Form login: {login2.status_code}")
                if login2.status_code != 200:
                    print(f"Both login methods failed. JSON resp: {login.text[:200]}")
                    print(f"Form resp: {login2.text[:200]}")
                    return

            headers = headers_json

            # List inbounds to find inbound 3
            resp = await client.get(f"{panel_url}/panel/api/inbounds/list", headers=headers)
            print(f"List inbounds: {resp.status_code}")
            if resp.status_code != 200:
                print("Could not list inbounds")
                return

            inbounds = resp.json().get("obj", [])
            print(f"Total inbounds: {len(inbounds)}")
            for ib in inbounds:
                settings = ib.get("settings", "{}")
                if isinstance(settings, str):
                    settings = json.loads(settings) if settings else {}
                clients = settings.get("clients", [])
                print(f"  Inbound {ib['id']} port={ib['port']}: {len(clients)} clients")

            # Find inbound 3
            ib3 = next((ib for ib in inbounds if ib["id"] == 3), None)
            if not ib3:
                print("\nInbound 3 not found on LA panel — already deleted?")
                return

            print(f"\nInbound 3 found: port={ib3['port']}, clearing clients...")
            settings = ib3.get("settings", "{}")
            if isinstance(settings, str):
                settings = json.loads(settings)
            client_count = len(settings.get("clients", []))
            print(f"  Current clients: {client_count}")

            # Read-modify-write: send FULL inbound update (3x-ui needs all fields)
            settings["clients"] = []
            stream = ib3.get("streamSettings", {})
            sniffing = ib3.get("sniffing", {})
            update_body = {
                "up": ib3.get("up", 0),
                "down": ib3.get("down", 0),
                "total": ib3.get("total", 0),
                "remark": ib3.get("remark", ""),
                "enable": ib3.get("enable", True),
                "expiryTime": ib3.get("expiryTime", 0),
                "listen": ib3.get("listen", ""),
                "port": ib3.get("port", ""),
                "protocol": ib3.get("protocol", "vless"),
                "settings": json.dumps(settings),
                "streamSettings": json.dumps(stream) if isinstance(stream, dict) else stream,
                "sniffing": json.dumps(sniffing) if isinstance(sniffing, dict) else sniffing,
            }

            resp = await client.post(
                f"{panel_url}/panel/api/inbounds/update/3",
                headers=headers,
                json=update_body
            )
            print(f"Update (full, clear clients): {resp.status_code}")
            if resp.status_code == 200:
                print(f"  success={resp.json().get('success', '?')}")
                print(f"  msg={resp.json().get('msg', '?')[:100]}")

            # Try to delete the inbound entirely
            resp = await client.post(
                f"{panel_url}/panel/api/inbounds/delInbound",
                headers=headers,
                json={"id": 3}
            )
            print(f"delInbound 3: {resp.status_code} success={resp.json().get('success', False) if resp.status_code == 200 else 'N/A'}")

            # Verify
            resp = await client.get(f"{panel_url}/panel/api/inbounds/get/3", headers=headers)
            if resp.status_code == 200:
                ib = resp.json().get("obj", {})
                if ib:
                    s = ib.get("settings", "{}")
                    if isinstance(s, str):
                        s = json.loads(s)
                    print(f"\nInbound 3 still exists with {len(s.get('clients',[]))} clients")
                else:
                    print("\nInbound 3 deleted successfully")
            else:
                print(f"\nInbound 3 get returned {resp.status_code} (likely deleted)")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
