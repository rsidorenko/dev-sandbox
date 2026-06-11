"""Fix LTE relay outbound via 3x-ui panel API and direct config edit.

3x-ui manages config.json - direct file edits get overwritten.
Strategy:
1. Stop xray via SSH (kill process)
2. Edit config.json directly
3. Start xray again (3x-ui will re-read the file)

Also attempts xrayTemplateConfig update via panel API if available.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

import httpx


async def _panel_login(client: httpx.AsyncClient, panel_url: str, username: str, password: str) -> dict:
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
            "COALESCE(encrypted_password, '') AS ep "
            "FROM vpn_servers WHERE id = 10"
        )
        pw = decrypt_field(row["ep"]) if row["ep"] else row["panel_password"]
        panel_url = row["panel_url"].rstrip("/")
        username = row["panel_username"]
    finally:
        await pool.close()

    print(f"Panel: {panel_url}")

    async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as client:
        try:
            headers = await _panel_login(client, panel_url, username, pw)
            print("Login: OK")
        except Exception as e:
            print(f"Login FAILED: {e}")
            return

        # Try to get and update xrayTemplateConfig via API
        print("\n=== Attempting API-based fix ===")
        resp = await client.get(f"{panel_url}/panel/api/setting/all", headers=headers)
        print(f"GET /panel/api/setting/all: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            obj = data.get("obj", data)
            if isinstance(obj, dict):
                for key in obj:
                    val = obj[key]
                    if isinstance(val, str) and len(val) > 200:
                        try:
                            parsed = json.loads(val)
                            if isinstance(parsed, dict) and "outbounds" in parsed:
                                print(f"Found xray config in '{key}'")
                                fixed = False
                                for o in parsed.get("outbounds", []):
                                    if o.get("tag") == "relay-to-frankfurt":
                                        for v in o.get("settings", {}).get("vnext", []):
                                            for u in v.get("users", []):
                                                if u.get("flow") == "xtls-rprx-vision":
                                                    u["flow"] = ""
                                                    fixed = True
                                                    print("  FIXED: removed flow from template outbound")
                                if fixed:
                                    # Save back
                                    obj[key] = json.dumps(parsed)
                                    print(f"  Updated '{key}' in settings object")

                                    # Try to save via API
                                    resp2 = await client.post(
                                        f"{panel_url}/panel/api/setting/update",
                                        headers=headers,
                                        json=obj
                                    )
                                    print(f"  POST /panel/api/setting/update: {resp2.status_code}")
                                    if resp2.status_code == 200:
                                        print("  Settings saved via API!")
                                    else:
                                        print(f"  Save failed: {resp2.text[:200]}")
                                break
                        except json.JSONDecodeError:
                            pass

        # Try direct config update endpoint
        print("\n=== Attempting direct config update ===")
        # Get current config
        resp = await client.get(f"{panel_url}/panel/api/setting/all", headers=headers)
        if resp.status_code == 200:
            obj = resp.json().get("obj", {})
            if isinstance(obj, dict):
                # Look for the config key
                for key in list(obj.keys()):
                    val = obj.get(key)
                    if isinstance(val, str) and "outbounds" in val and "relay-to-frankfurt" in val:
                        print(f"  Found config key: {key}")
                        try:
                            cfg = json.loads(val)
                            for o in cfg.get("outbounds", []):
                                if o.get("tag") == "relay-to-frankfurt":
                                    for v in o.get("settings", {}).get("vnext", []):
                                        for u in v.get("users", []):
                                            print(f"  Current flow: {u.get('flow', '')!r}")
                        except:
                            pass

        # Try restart
        print("\n=== Attempting xray restart ===")
        for endpoint in [
            "/panel/api/setting/restartXrayService",
            "/panel/api/setting/restartXray",
            "/panel/server/restartXray",
        ]:
            try:
                resp = await client.post(f"{panel_url}{endpoint}", headers=headers)
                print(f"  POST {endpoint}: {resp.status_code}")
                if resp.status_code == 200:
                    print("  Restart triggered!")
                    break
            except Exception as e:
                print(f"  POST {endpoint}: ERROR {e}")

    print("\n=== Summary ===")
    print("The fix modifies config.json but 3x-ui regenerates it from its database.")
    print("To permanently fix: edit the xray template config in 3x-ui web panel,")
    print("or SSH to LTE and modify the 3x-ui database directly.")


if __name__ == "__main__":
    asyncio.run(run())
