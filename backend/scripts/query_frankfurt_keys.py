"""Get Frankfurt Reality public key + shortId for relay config."""
import asyncio, os, sys, json, re
import httpx

async def run():
    import asyncpg
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field
        # Server 5 = Frankfurt 3.0 (xhttp inbound on 8443), server 4 = Frankfurt 1.0 (tcp 443)
        rows = await pool.fetch(
            "SELECT id, label, panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password,'') AS ep, inbound_id, reality_pbk, reality_sid "
            "FROM vpn_servers WHERE id IN (4,5,6) ORDER BY id")
        for r in rows:
            print(f"SERVER {r['id']} {r['label']}: inbound_id={r['inbound_id']} "
                  f"reality_pbk={r['reality_pbk']} reality_sid={r['reality_sid']}")
        # Get actual keys from panel for inbound 2 (8443 xhttp)
        r5 = [r for r in rows if r['id']==5][0]
        panel = r5['panel_url'].rstrip('/')
        pw = decrypt_field(r5['ep']) if r5['ep'] else r5['panel_password']
        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as c:
            page = await c.get(f"{panel}/")
            csrf=None
            m = re.search(r'csrf-token" content="([^"]+)"', page.text)
            if m: csrf=m.group(1)
            h={"Content-Type":"application/json"}
            if csrf: h["X-CSRF-Token"]=csrf
            await c.post(f"{panel}/login", json={"username":r5['panel_username'],"password":pw}, headers=h)
            resp = await c.get(f"{panel}/panel/api/inbounds/get/2", headers=h)
            ib = resp.json().get("obj",{})
            rs = ib.get("streamSettings",{}).get("realitySettings",{})
            print(f"\nFrankfurt inbound 2 (8443 xhttp/reality):")
            print(f"  privateKey={rs.get('privateKey','')}")
            print(f"  serverNames={rs.get('serverNames',[])}")
            print(f"  shortIds={rs.get('shortIds',[])}")
            print(f"  dest={rs.get('dest','')}")
            # Derive public key
            import base64
            from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            priv = rs.get('privateKey','')
            if priv:
                pb = base64.urlsafe_b64decode(priv+"==")
                k = X25519PrivateKey.from_private_bytes(pb)
                pub = k.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
                print(f"  DERIVED publicKey={base64.urlsafe_b64encode(pub).decode().rstrip('=')}")
    finally:
        await pool.close()

asyncio.run(run())
