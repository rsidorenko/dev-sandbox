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

        # 1. Check xray status
        print("=== XRAY STATUS ===")
        for endpoint in [
            "/panel/api/setting/status",
            "/panel/server/status",
            "/panel/api/status",
        ]:
            try:
                resp = await client.get(f"{panel_url}{endpoint}", headers=headers)
                print(f"  GET {endpoint}: {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                print(f"  GET {endpoint}: ERROR {e}")

        # 2. List all inbounds with details
        print("\n=== INBOUNDS ===")
        resp = await client.get(f"{panel_url}/panel/api/inbounds/list", headers=headers)
        if resp.status_code == 200:
            for ib in resp.json().get("obj", []):
                settings = ib.get("settings", {})
                if isinstance(settings, str):
                    settings = json.loads(settings)
                clients = settings.get("clients", [])
                stream = ib.get("stream", {})
                print(f"  Inbound {ib['id']}: port={ib['port']} protocol={ib['protocol']} "
                      f"enable={ib.get('enable', '?')} clients={len(clients)}")
                # Check first client
                if clients:
                    c = clients[0]
                    print(f"    sample: email={c.get('email')} flow=\"{c.get('flow','')}\" "
                          f"uuid={c.get('id','')[:12]}...")

        # 3. Try different restart endpoints
        print("\n=== RESTART XRAY ===")
        for endpoint in [
            "/panel/api/setting/restartXrayService",
            "/panel/api/setting/restartXray",
            "/panel/server/restartXray",
        ]:
            try:
                resp = await client.post(f"{panel_url}{endpoint}", headers=headers)
                status = "OK" if resp.status_code == 200 else f"FAIL({resp.status_code})"
                body = resp.text[:100] if resp.status_code != 200 else ""
                print(f"  POST {endpoint}: {status} {body}")
            except Exception as e:
                print(f"  POST {endpoint}: ERROR {e}")

        # 4. Get xray config (template)
        print("\n=== XRAY CONFIG ===")
        resp = await client.get(f"{panel_url}/panel/api/setting/all", headers=headers)
        if resp.status_code == 200:
            data = resp.json().get("obj", {})
            if isinstance(data, dict):
                # Look for xray config/template
                for key in ["xrayTemplateConfig", "xraySetting", "config"]:
                    if key in data:
                        val = data[key]
                        if isinstance(val, str) and len(val) > 50:
                            try:
                                cfg = json.loads(val)
                                print(f"  {key}: parsed OK")
                                # Show outbounds
                                outbounds = cfg.get("outbounds", [])
                                print(f"    outbounds ({len(outbounds)}):")
                                for ob in outbounds:
                                    print(f"      - {ob.get('tag','?')}: {ob.get('protocol','?')} "
                                          f"→ {ob.get('settings',{}).get('vnext',[{}])[0].get('address','?') if ob.get('protocol')=='vless' else ''}")
                                # Show routing
                                routing = cfg.get("routing", {})
                                rules = routing.get("rules", [])
                                print(f"    routing rules ({len(rules)}):")
                                for r in rules[:5]:
                                    print(f"      - type={r.get('type','?')} outbound={r.get('outboundTag','?')} "
                                          f"domain={r.get('domain',[])}")
                                if len(rules) > 5:
                                    print(f"      ... and {len(rules)-5} more")
                            except json.JSONDecodeError:
                                print(f"  {key}: {val[:200]}")
                        else:
                            print(f"  {key}: {str(val)[:200]}")
            else:
                print(f"  resp.obj type: {type(data)}")
        else:
            print(f"  GET all: {resp.status_code}")

        # 5. Check xray logs
        print("\n=== XRAY LOGS (last errors) ===")
        resp = await client.get(f"{panel_url}/panel/api/xray/logs", headers=headers)
        if resp.status_code == 200:
            lines = resp.text.split("\n")[-10:]
            for l in lines:
                if l.strip():
                    print(f"  {l[:150]}")
        else:
            print(f"  logs endpoint: {resp.status_code}")


if __name__ == "__main__":
    asyncio.run(run())
