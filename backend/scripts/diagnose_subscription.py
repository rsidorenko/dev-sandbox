"""Diagnose why a subscription's keys don't connect.
Checks: user UUID in DB, UUID present as client on each panel, panel login."""
import asyncio, os, re, json
import httpx

TOKEN = "1P0XAUqZwSP5oaTM7MmsAw"

async def _login(client, panel, user, pw):
    page = await client.get(f"{panel}/")
    csrf = None
    m = re.search(r'csrf-token" content="([^"]+)"', page.text)
    if m: csrf = m.group(1)
    h = {"Content-Type": "application/json"}
    if csrf: h["X-CSRF-Token"] = csrf
    r = await client.post(f"{panel}/login", json={"username": user, "password": pw}, headers=h)
    return h if (r.status_code == 200 and r.json().get("success")) else None

async def run():
    import asyncpg
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field
        # Find user by token
        row = await pool.fetchrow(
            "SELECT internal_user_id, vless_uuid FROM user_identities WHERE subscription_token = $1", TOKEN)
        if not row:
            print(f"ERROR: no user with subscription_token={TOKEN}")
            return
        uid = row["internal_user_id"]
        uuid = row["vless_uuid"]
        print(f"User: {uid}")
        print(f"DB vless_uuid: {uuid}")
        print()

        servers = await pool.fetch(
            "SELECT id, label, panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password,'') AS ep, inbound_id, server_host, server_port, "
            "transport_type, reality_pbk, reality_sid, reality_sni "
            "FROM vpn_servers WHERE is_active = TRUE ORDER BY id")

        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as client:
            for s in servers:
                pw = decrypt_field(s["ep"]) if s["ep"] else s["panel_password"]
                panel = s["panel_url"].rstrip("/")
                print(f"=== {s['label']} (id={s['id']}) inbound={s['inbound_id']} ===")
                print(f"  host={s['server_host']}:{s['server_port']} transport={s['transport_type']}")
                # group by panel to login once
                try:
                    h = await _login(client, panel, s["panel_username"], pw)
                    if not h:
                        print("  PANEL LOGIN FAILED")
                        continue
                    resp = await client.get(f"{panel}/panel/api/inbounds/get/{s['inbound_id']}", headers=h)
                    if resp.status_code != 200:
                        print(f"  get inbound failed: {resp.status_code}")
                        continue
                    ib = resp.json().get("obj", {})
                    settings = ib.get("settings", "{}")
                    if isinstance(settings, str): settings = json.loads(settings)
                    clients = settings.get("clients", [])
                    match = [c for c in clients if c.get("id") == uuid]
                    print(f"  clients on inbound: {len(clients)}")
                    print(f"  UUID {uuid[:12]}... present: {'YES' if match else 'NO ❌'}")
                    if match:
                        c = match[0]
                        print(f"    enable={c.get('enable')} flow={c.get('flow','')!r} email={c.get('email')}")
                except Exception as e:
                    print(f"  ERROR: {e}")
                print()
    finally:
        await pool.close()

asyncio.run(run())
