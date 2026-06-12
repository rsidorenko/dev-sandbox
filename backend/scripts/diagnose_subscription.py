"""Diagnose: pbk consistency (DB reality_pbk vs server's actual derived pubkey)."""
import asyncio, os, re, json, base64
import httpx

async def _login(client, panel, user, pw):
    page = await client.get(f"{panel}/")
    csrf = None
    m = re.search(r'csrf-token" content="([^"]+)"', page.text)
    if m: csrf = m.group(1)
    h = {"Content-Type": "application/json"}
    if csrf: h["X-CSRF-Token"] = csrf
    r = await client.post(f"{panel}/login", json={"username": user, "password": pw}, headers=h)
    return h if (r.status_code == 200 and r.json().get("success")) else None

def derive_pubkey(privkey):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    pb = base64.urlsafe_b64decode(privkey + "==")
    k = X25519PrivateKey.from_private_bytes(pb)
    pub = k.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(pub).decode().rstrip("=")

async def run():
    import asyncpg
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field
        servers = await pool.fetch(
            "SELECT id, label, panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password,'') AS ep, inbound_id, reality_pbk "
            "FROM vpn_servers WHERE is_active = TRUE ORDER BY id")
        seen_panels = {}
        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as client:
            for s in servers:
                panel = s["panel_url"].rstrip("/")
                ib_id = s["inbound_id"]
                key = (panel, ib_id)
                print(f"=== {s['label']} (id={s['id']}) inbound={ib_id} ===")
                print(f"  DB reality_pbk (in link): {s['reality_pbk']}")
                if key not in seen_panels:
                    pw = decrypt_field(s["ep"]) if s["ep"] else s["panel_password"]
                    h = await _login(client, panel, s["panel_username"], pw)
                    if not h:
                        print("  PANEL LOGIN FAILED"); continue
                    resp = await client.get(f"{panel}/panel/api/inbounds/get/{ib_id}", headers=h)
                    ib = resp.json().get("obj", {})
                    stream = ib.get("streamSettings", {})
                    if isinstance(stream, str): stream = json.loads(stream)
                    rs = stream.get("realitySettings", {})
                    privkey = rs.get("privateKey", "")
                    actual_pub = derive_pubkey(privkey) if privkey else ""
                    seen_panels[key] = actual_pub
                actual_pub = seen_panels[key]
                print(f"  server derived pubkey:    {actual_pub}")
                match = (s["reality_pbk"] == actual_pub)
                print(f"  MATCH: {'YES ✅' if match else 'NO ❌❌❌ — LINK HAS WRONG PBK'}")
                print()
    finally:
        await pool.close()

asyncio.run(run())
