"""Diagnose: panel_url + webBasePath + pbk consistency + login debug."""
import asyncio, os, re, json, base64
import httpx

async def _try_login(client, panel_url, user, pw):
    """Try login and return (headers_or_None, diagnostics_string)."""
    diag = []
    # Step 1: GET / (session cookie + detect webBasePath)
    try:
        page = await client.get(f"{panel_url}/")
        diag.append(f"GET / -> {page.status_code}")
        if page.status_code == 404:
            diag.append("  ROOT 404: panel likely has webBasePath not in panel_url")
        # Check if page redirects or contains 3x-ui hints
        if 'x-ui' in page.text.lower() or '3x-ui' in page.text.lower():
            diag.append("  Page contains 'x-ui' — panel serves at root (no webBasePath needed)")
        # Extract CSRF
        csrf = None
        m = re.search(r'csrf-token" content="([^"]+)"', page.text)
        if m:
            csrf = m.group(1)
            diag.append(f"  CSRF from meta: {csrf[:16]}...")
    except Exception as e:
        diag.append(f"GET / FAILED: {e}")
        return None, "\n".join(diag)

    # Step 2: Try /csrf-token endpoint
    try:
        csrf_resp = await client.get(f"{panel_url}/csrf-token")
        diag.append(f"GET /csrf-token -> {csrf_resp.status_code}")
        if csrf_resp.status_code == 200:
            data = csrf_resp.json()
            csrf = data.get("obj", "") or data.get("token", "") or csrf
            if csrf:
                diag.append(f"  CSRF from endpoint: {csrf[:16]}...")
    except Exception:
        pass

    # Step 3: POST /login
    h = {"Content-Type": "application/json"}
    if csrf:
        h["X-CSRF-Token"] = csrf
    try:
        r = await client.post(f"{panel_url}/login", json={"username": user, "password": pw}, headers=h)
        diag.append(f"POST /login -> {r.status_code}")
        if r.status_code == 200:
            body = r.json()
            diag.append(f"  success={body.get('success')} msg={body.get('msg', '')}")
            if body.get("success"):
                return h, "\n".join(diag)
        else:
            diag.append(f"  body: {r.text[:200]}")
    except Exception as e:
        diag.append(f"POST /login FAILED: {e}")
    return None, "\n".join(diag)

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
            "COALESCE(encrypted_password,'') AS ep, inbound_id, reality_pbk, "
            "server_host, server_port, transport_type, is_active "
            "FROM vpn_servers ORDER BY id")
        print(f"Total servers: {len(servers)}, active: {sum(1 for s in servers if s['is_active'])}")
        print()

        seen_panels = {}
        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as client:
            for s in servers:
                active = "ACTIVE" if s["is_active"] else "INACTIVE"
                panel = s["panel_url"].rstrip("/")
                ib_id = s["inbound_id"]
                print(f"=== {s['label']} (id={s['id']}) [{active}] ===")
                print(f"  panel_url: {panel}")
                print(f"  inbound_id: {ib_id}, transport: {s['transport_type']}")
                print(f"  host: {s['server_host']}:{s['server_port']}")

                if not s["is_active"]:
                    print("  SKIPPED (inactive)\n")
                    continue

                pw = decrypt_field(s["ep"]) if s["ep"] else s["panel_password"]
                key = (panel, ib_id)

                if key not in seen_panels:
                    h, diag = await _try_login(client, panel, s["panel_username"], pw)
                    print(f"  Login diagnostics:")
                    for line in diag.split("\n"):
                        print(f"    {line}")
                    if not h:
                        print("  ❌ PANEL LOGIN FAILED")
                        seen_panels[key] = None
                    else:
                        resp = await client.get(f"{panel}/panel/api/inbounds/get/{ib_id}", headers=h)
                        ib = resp.json().get("obj", {})
                        stream = ib.get("streamSettings", {})
                        if isinstance(stream, str): stream = json.loads(stream)
                        rs = stream.get("realitySettings", {})
                        privkey = rs.get("privateKey", "")
                        actual_pub = derive_pubkey(privkey) if privkey else ""
                        seen_panels[key] = actual_pub
                        print(f"  ✅ Login OK, reality_pbk derived: {actual_pub[:24]}...")
                else:
                    print(f"  (panel already checked above)")

                actual_pub = seen_panels.get(key)
                if actual_pub is not None:
                    match = (s["reality_pbk"] == actual_pub)
                    print(f"  DB pbk: {s['reality_pbk']}")
                    print(f"  MATCH: {'YES ✅' if match else 'NO ❌'}")
                print()
    finally:
        await pool.close()

asyncio.run(run())
