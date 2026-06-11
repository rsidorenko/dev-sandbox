import asyncio, asyncpg, os, sys, json, httpx, re

async def run():
    dsn = os.environ.get("DATABASE_URL", "")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field
        row = await pool.fetchrow("SELECT panel_url, panel_username, panel_password, COALESCE(encrypted_password,'') AS ep, server_host, server_port FROM vpn_servers WHERE id = 10")
        pw = decrypt_field(row["ep"]) if row["ep"] else row["panel_password"]
        host = row["server_host"]
        port = row["server_port"]
        panel = row["panel_url"].rstrip("/")
        print(f"LTE host: {host}:{port}")
        print(f"Panel: {panel}")

        # Check if VPN port is listening
        async with httpx.AsyncClient(verify=False, timeout=5) as c:
            try:
                r = await c.get(f"https://{host}:{port}/", follow_redirects=False)
                print(f"VPN port {port}: HTTP {r.status_code} (xray is LISTENING)")
            except httpx.ConnectError as e:
                print(f"VPN port {port}: CONNECT FAILED - {e}")
            except Exception as e:
                print(f"VPN port {port}: {type(e).__name__}: {e}")

            # Check port 80 (CDN inbound)
            try:
                r = await c.get(f"http://{host}:80/")
                print(f"CDN port 80: HTTP {r.status_code}")
            except Exception as e:
                print(f"CDN port 80: {type(e).__name__}: {e}")

        # Try to get xrayTemplateConfig via the panel
        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=10) as c:
            page = await c.get(f"{panel}/")
            csrf = None
            m = re.search(r'csrf-token" content="([^"]+)"', page.text)
            if m: csrf = m.group(1)
            h = {"Content-Type": "application/json"}
            if csrf: h["X-CSRF-Token"] = csrf
            login = await c.post(f"{panel}/login", json={"username": row["panel_username"], "password": pw}, headers=h)
            if login.status_code != 200:
                print(f"Login failed: {login.status_code}")
                return
            print("Panel login OK")

            # Get all settings
            r = await c.get(f"{panel}/panel/api/setting/all", headers=h)
            print(f"Settings API: {r.status_code}")
            if r.status_code == 200:
                obj = r.json().get("obj", {})
                if isinstance(obj, dict):
                    for k in sorted(obj.keys()):
                        v = obj[k]
                        if isinstance(v, str) and len(v) > 200:
                            print(f"  {k}: ({len(v)} chars)")
                            # Parse xrayTemplateConfig
                            if "xray" in k.lower() or "template" in k.lower():
                                try:
                                    cfg = json.loads(v)
                                    obs = cfg.get("outbounds", [])
                                    print(f"    outbounds: {json.dumps(obs, indent=2)[:500]}")
                                    rts = cfg.get("routing", {}).get("rules", [])
                                    print(f"    routing rules ({len(rts)}):")
                                    for rt in rts[:5]:
                                        print(f"      {rt.get('type','?')} -> {rt.get('outboundTag','?')} domains={rt.get('domain',[])}")
                                except:
                                    print(f"    (not valid JSON)")
                        else:
                            print(f"  {k}: {str(v)[:100]}")
                elif isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, dict):
                            k = item.get("key", "?")
                            v = item.get("value", "")
                            if isinstance(v, str) and len(v) > 200:
                                print(f"  {k}: ({len(v)} chars)")
                                if "xray" in k.lower() or "template" in k.lower():
                                    try:
                                        cfg = json.loads(v)
                                        obs = cfg.get("outbounds", [])
                                        print(f"    outbounds:")
                                        for o in obs:
                                            print(f"      {o.get('tag','?')}: {o.get('protocol','?')} -> {o.get('settings',{}).get('vnext',[{}])[0].get('address','')}")
                                    except:
                                        pass
                            else:
                                print(f"  {k}: {str(v)[:100]}")
    finally:
        await pool.close()

asyncio.run(run())
