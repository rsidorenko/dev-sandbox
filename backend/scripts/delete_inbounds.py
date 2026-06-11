"""Fully delete inbound ports from 3x-ui panels (not just clients).

Targets:
- LTE panel (62.84.118.140): inbound 1 (port 443), inbound 6 (port 80)
- LA panel (216.227.169.120): inbound 3 (port 8080)

Strategy: try multiple delInbound API endpoint variants. If all fail,
report so we can fall back to SSH + SQLite deletion.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

import httpx

# (server_id_for_creds, panel_url, inbound_ids_to_delete)
TARGETS = [
    (7, "https://216.227.169.120:2053", [3]),          # LA inbound 3
    (9, "https://62.84.118.140:54023/Cq6xxAccNLaSEBcR0L", [1, 6]),  # LTE inbounds 1, 6
]

# delInbound endpoint variants to try (method, path_template)
DEL_ENDPOINTS = [
    ("POST", "/panel/api/inbounds/delInbound"),      # newer
    ("POST", "/panel/inbounds/delInbound"),          # older, no /api/
    ("DELETE", "/panel/api/inbounds/{id}"),          # REST style
    ("POST", "/panel/api/inbounds/{id}/del"),        # alt
    ("POST", "/panel/api/inbounds/delete"),          # alt
]


async def _panel_login(client, panel_url, username, password):
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


async def try_delete_inbound(client, panel_url, headers, inbound_id):
    """Try all delInbound endpoint variants. Return True if any succeeded."""
    for method, path_template in DEL_ENDPOINTS:
        path = path_template.replace("{id}", str(inbound_id))
        url = f"{panel_url}{path}"
        try:
            if method == "POST":
                if "{id}" in path_template:
                    resp = await client.post(url, headers=headers)
                else:
                    resp = await client.post(url, headers=headers, json={"id": inbound_id})
            else:  # DELETE
                resp = await client.delete(url, headers=headers)

            success = (resp.status_code == 200 and
                       resp.json().get("success", False) if resp.headers.get("content-type","").startswith("application/json") else resp.status_code == 200)
            status_str = f"{resp.status_code}"
            if resp.status_code == 200:
                try:
                    status_str += f" success={resp.json().get('success')}"
                except:
                    pass
            print(f"    {method} {path}: {status_str}")
            if success:
                return True
        except Exception as e:
            print(f"    {method} {path}: ERROR {e}")
    return False


async def run():
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field

        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=20) as client:
            for cred_server_id, panel_url, inbound_ids in TARGETS:
                row = await pool.fetchrow(
                    "SELECT panel_username, panel_password, COALESCE(encrypted_password,'') AS ep "
                    "FROM vpn_servers WHERE id = $1", cred_server_id)
                if not row:
                    print(f"\nERROR: cred server {cred_server_id} not found")
                    continue
                pw = decrypt_field(row["ep"]) if row["ep"] else row["panel_password"]

                print(f"\n{'='*60}")
                print(f"PANEL: {panel_url} (creds from server {cred_server_id})")
                print(f"Targets: inbounds {inbound_ids}")
                print(f"{'='*60}")

                try:
                    headers = await _panel_login(client, panel_url, row["panel_username"], pw)
                    print("Login: OK")
                except Exception as e:
                    print(f"Login FAILED: {e}")
                    continue

                # Show current inbounds
                resp = await client.get(f"{panel_url}/panel/api/inbounds/list", headers=headers)
                if resp.status_code == 200:
                    inbounds = resp.json().get("obj", [])
                    print(f"Current inbounds: {[(ib['id'], ib['port']) for ib in inbounds]}")

                for ib_id in inbound_ids:
                    print(f"\n  Deleting inbound {ib_id}...")
                    deleted = await try_delete_inbound(client, panel_url, headers, ib_id)
                    if deleted:
                        print(f"  ✅ inbound {ib_id} DELETED via API")
                    else:
                        print(f"  ❌ inbound {ib_id} API delete failed - needs SSH/SQLite fallback")

                # Verify
                resp = await client.get(f"{panel_url}/panel/api/inbounds/list", headers=headers)
                if resp.status_code == 200:
                    inbounds = resp.json().get("obj", [])
                    print(f"\nRemaining inbounds: {[(ib['id'], ib['port']) for ib in inbounds]}")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
