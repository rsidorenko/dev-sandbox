"""Register the RU relay UUID as a client on Helsinki's :443 inbound.

The relay outbound on 89.169.139.153 connects to Helsinki:443 with VLESS+Reality
using RELAY_UUID. Helsinki requires client auth, so RELAY_UUID must be registered
here or the relay connection is rejected ("invalid request user id").

Uses the SAME proven read-modify-write `update/{inbound_id}` path the bot uses for
regular users (XuiApiClient._add_client_via_update) — the 3x-ui v3.3.0 panel handler
populates the `clients`/`client_inbounds` tables from the settings JSON, so no
direct SQLite write is needed (unlike the buggy LTE SQLite-direct approach).

Auto-detects the Helsinki inbound on port 443 (does not hardcode inbound_id).
Idempotent: skips if RELAY_UUID already present.

Runs in the production container with DATABASE_URL + FIELD_ENCRYPTION_KEY.
"""

import asyncio
import json
import os
import re
import sys

import httpx

RELAY_UUID = "00607f0b-a9e7-4280-abb3-2231e1b9c2ff"
RELAY_EMAIL = "relay-from-ru-relay"
HELSINKI_HOST = "77.221.159.106"
TARGET_PORT = 443


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
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field
        row = await pool.fetchrow(
            "SELECT panel_url, panel_username, panel_password, COALESCE(encrypted_password,'') AS ep "
            "FROM vpn_servers WHERE server_host = $1 ORDER BY id LIMIT 1", HELSINKI_HOST)
        if not row:
            print(f"ERROR: no vpn_servers row for {HELSINKI_HOST}", file=sys.stderr)
            sys.exit(1)
        pw = decrypt_field(row["ep"]) if row["ep"] else row["panel_password"]
        panel = row["panel_url"].rstrip("/")

        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=20) as client:
            headers = await _login(client, panel, row["panel_username"], pw)
            print("Helsinki login: OK")

            # Find the :443 vless inbound (the relay target).
            resp = await client.get(f"{panel}/panel/api/inbounds/list", headers=headers)
            inbounds = resp.json().get("obj", []) or []
            target = None
            for ib in inbounds:
                try:
                    if int(ib.get("port", 0)) == TARGET_PORT and ib.get("protocol") == "vless":
                        target = ib
                        break
                except (ValueError, TypeError):
                    continue
            if not target:
                print(f"ERROR: no vless inbound on port {TARGET_PORT} found on Helsinki", file=sys.stderr)
                print(f"  available: {[(ib.get('id'), ib.get('port'), ib.get('protocol')) for ib in inbounds]}")
                sys.exit(1)

            inbound_id = target["id"]
            print(f"Target inbound: id={inbound_id} port={TARGET_PORT} remark={target.get('remark')!r}")

            settings = target.get("settings", "{}")
            if isinstance(settings, str):
                settings = json.loads(settings)
            clients = settings.get("clients", [])

            existing = [c for c in clients if c.get("id") == RELAY_UUID]
            if existing:
                print(f"Relay UUID already present on inbound {inbound_id}: {existing[0].get('email')}")
                return

            new_client = {
                "id": RELAY_UUID, "email": RELAY_EMAIL, "enable": True,
                "expiryTime": 0, "limitIp": 0, "subId": "", "tgId": "", "reset": 0,
                "flow": "",  # NO xtls-rprx-vision on relay client
            }
            clients.append(new_client)
            settings["clients"] = clients

            stream = target.get("streamSettings", {})
            sniffing = target.get("sniffing", {})
            update_body = {
                "up": target.get("up", 0), "down": target.get("down", 0),
                "total": target.get("total", 0), "remark": target.get("remark", ""),
                "enable": target.get("enable", True), "expiryTime": target.get("expiryTime", 0),
                "listen": target.get("listen", ""), "port": target.get("port", ""),
                "protocol": target.get("protocol", "vless"),
                "settings": json.dumps(settings),
                "streamSettings": json.dumps(stream) if isinstance(stream, dict) else stream,
                "sniffing": json.dumps(sniffing) if isinstance(sniffing, dict) else sniffing,
            }
            resp = await client.post(f"{panel}/panel/api/inbounds/update/{inbound_id}",
                                     headers=headers, json=update_body)
            ok = resp.status_code == 200 and resp.json().get("success", False)
            print(f"Add relay client to inbound {inbound_id}: {resp.status_code} success={ok}")
            if not ok:
                print(f"  response: {resp.text[:300]}", file=sys.stderr)
                sys.exit(1)

            # Verify
            resp = await client.get(f"{panel}/panel/api/inbounds/list", headers=headers)
            ib2 = next((x for x in (resp.json().get("obj") or []) if x.get("id") == inbound_id), {})
            s2 = ib2.get("settings", "{}")
            if isinstance(s2, str):
                s2 = json.loads(s2)
            relay_in = [c for c in s2.get("clients", []) if c.get("id") == RELAY_UUID]
            print(f"Verified: relay client present = {bool(relay_in)}, "
                  f"total clients = {len(s2.get('clients', []))}")
            print("\nNOTE: if Helsinki xray still rejects the relay UUID, run sync_clients "
                  "(settings -> clients table) or the SSH SQLite fallback.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
