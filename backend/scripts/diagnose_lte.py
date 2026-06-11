"""Diagnose LTE server: check xray status, inbounds, outbounds, restart."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

import httpx


async def run() -> None:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field

        row = await pool.fetchrow(
            "SELECT panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password, '') AS encrypted_password "
            "FROM vpn_servers WHERE id = 10"
        )
        panel_url = row["panel_url"].rstrip("/")
        password = row["encrypted_password"] if row["encrypted_password"] else row["panel_password"]
        if row["encrypted_password"]:
            password = decrypt_field(row["encrypted_password"])
        username = row["panel_username"]
    finally:
        await pool.close()

    print(f"Panel: {panel_url}")
    print(f"User: {username}")

    async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as client:
        # Login
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
            print(f"LOGIN FAILED: {login.status_code}")
            return
        print("Login OK\n")

        # 1. List all inbounds with details
        print("=== INBOUNDS ===")
        resp = await client.get(f"{panel_url}/panel/api/inbounds/list", headers=headers)
        if resp.status_code == 200:
            inbounds = resp.json().get("obj", [])
            print(f"  Total inbounds: {len(inbounds)}")
            for ib in inbounds:
                settings = ib.get("settings", {})
                if isinstance(settings, str):
                    settings = json.loads(settings)
                clients = settings.get("clients", [])
                stream = ib.get("streamSettings", {})
                net = stream.get("network", "?")
                sec = stream.get("security", "?")
                print(f"  ID={ib['id']} port={ib['port']} proto={ib['protocol']} "
                      f"enable={ib.get('enable', '?')} net={net} sec={sec} "
                      f"clients={len(clients)}")
                # Show Reality details
                if "realitySettings" in stream:
                    rs = stream["realitySettings"]
                    print(f"    Reality: dest={rs.get('dest','?')[:50]} "
                          f"sni={rs.get('serverNames',[])} "
                          f"shortIds={rs.get('shortIds',[])}")
                if "wsSettings" in stream:
                    ws = stream["wsSettings"]
                    print(f"    WS: path={ws.get('path','?')} host={ws.get('headers',{}).get('Host','?')}")
                # Check first client
                if clients:
                    c = clients[0]
                    print(f"    client[0]: email={c.get('email')} flow=\"{c.get('flow','')}\" "
                          f"uuid={c.get('id','')[:12]}... limitIp={c.get('limitIp',0)}")

        # 2. Try to get xray config from various API endpoints
        print("\n=== XRAY CONFIG (via API) ===")
        config_found = False

        # Try /panel/api/setting/all
        resp = await client.get(f"{panel_url}/panel/api/setting/all", headers=headers)
        print(f"  /panel/api/setting/all: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            obj = data.get("obj", data)
            if isinstance(obj, dict):
                for key in sorted(obj.keys()):
                    val = obj[key]
                    if isinstance(val, str) and len(val) > 100:
                        print(f"    {key}: ({len(val)} chars)")
                        # Try to parse as JSON
                        try:
                            parsed = json.loads(val)
                            if isinstance(parsed, dict):
                                # Show keys
                                print(f"      keys: {list(parsed.keys())[:10]}")
                                # Show outbounds if present
                                outbounds = parsed.get("outbounds", [])
                                if outbounds:
                                    config_found = True
                                    print(f"      OUTBOUNDS ({len(outbounds)}):")
                                    for ob in outbounds:
                                        tag = ob.get("tag", "?")
                                        proto = ob.get("protocol", "?")
                                        addr = ""
                                        if ob.get("protocol") == "vless":
                                            vnext = ob.get("settings", {}).get("vnext", [])
                                            if vnext:
                                                addr = f"{vnext[0].get('address','?')}:{vnext[0].get('port','?')}"
                                        elif ob.get("protocol") == "freedom":
                                            addr = "direct"
                                        elif ob.get("protocol") == "blackhole":
                                            addr = "blocked"
                                        print(f"        {tag}: {proto} -> {addr}")
                                # Show routing rules
                                routing = parsed.get("routing", {})
                                rules = routing.get("rules", [])
                                if rules:
                                    print(f"      ROUTING rules ({len(rules)}):")
                                    for r in rules[:10]:
                                        print(f"        {r.get('type','?')} -> {r.get('outboundTag','?')} "
                                              f"domains={r.get('domain',[])[:3]} "
                                              f"network={r.get('network','')}")
                        except json.JSONDecodeError:
                            pass
                    elif isinstance(val, (list, dict)):
                        print(f"    {key}: {type(val).__name__} len={len(val)}")
                    else:
                        print(f"    {key}: {str(val)[:80]}")
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, dict):
                        k = item.get("key", item.get("id", "?"))
                        v = item.get("value", item.get("comment", ""))
                        if isinstance(v, str) and len(v) > 100:
                            print(f"    [{k}]: ({len(v)} chars)")
                        else:
                            print(f"    [{k}]: {str(v)[:80]}")

        # Try other config endpoints
        for endpoint in [
            "/panel/api/setting/config",
            "/panel/api/xray/config",
            "/panel/api/server/config",
            "/server/getConfig",
            "/panel/getConfig",
        ]:
            try:
                resp = await client.get(f"{panel_url}{endpoint}", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("obj"):
                        print(f"  {endpoint}: FOUND (status={resp.status_code})")
                        obj = data["obj"]
                        if isinstance(obj, str):
                            try:
                                obj = json.loads(obj)
                            except:
                                pass
                        if isinstance(obj, dict):
                            print(f"    keys: {list(obj.keys())[:10]}")
                            outbounds = obj.get("outbounds", [])
                            if outbounds:
                                config_found = True
                                print(f"    OUTBOUNDS ({len(outbounds)}):")
                                for ob in outbounds:
                                    print(f"      {ob.get('tag','?')}: {ob.get('protocol','?')}")
            except Exception:
                pass

        if not config_found:
            print("\n  WARNING: Could not find outbounds via API!")
            print("  SSH access required to read /usr/local/x-ui/bin/config.json")

        # 3. Try restart endpoints
        print("\n=== RESTART ATTEMPTS ===")
        for endpoint in [
            "/panel/api/setting/restartXrayService",
            "/panel/api/setting/restartXray",
            "/panel/server/restartXray",
        ]:
            try:
                resp = await client.post(f"{panel_url}{endpoint}", headers=headers)
                status = "OK" if resp.status_code == 200 else f"FAIL({resp.status_code})"
                print(f"  POST {endpoint}: {status}")
                if resp.status_code == 200:
                    print(f"    response: {resp.text[:100]}")
            except Exception as e:
                print(f"  POST {endpoint}: ERROR {e}")


if __name__ == "__main__":
    asyncio.run(run())
