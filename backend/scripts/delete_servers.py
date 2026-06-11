"""Investigate servers to delete: LA 2.0 CDN + LTE 10/12.

Shows current state and what will be deleted. Does NOT delete anything.
"""

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

        # List ALL servers
        rows = await pool.fetch(
            "SELECT id, label, server_host, server_port, transport_type, "
            "is_active, panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password, '') AS ep, inbound_id, "
            "country_flag "
            "FROM vpn_servers ORDER BY id"
        )

        print("=" * 70)
        print("ALL VPN SERVERS IN DATABASE")
        print("=" * 70)
        for r in rows:
            print(f"\n  ID={r['id']}: {r['country_flag']} {r['label']}")
            print(f"    host={r['server_host']}:{r['server_port']} transport={r['transport_type']}")
            print(f"    active={r['is_active']} inbound_id={r['inbound_id']}")
            print(f"    panel={r['panel_url']}")

        # Identify targets
        print("\n" + "=" * 70)
        print("DELETION TARGETS")
        print("=" * 70)
        targets = []
        for r in rows:
            label = (r["label"] or "").lower()
            srv_id = r["id"]
            # LA 2.0 CDN (inactive, LA, CDN transport)
            if (srv_id in (8, 9, 13, 14, 15, 16, 17) or
                ("los" in label or "angeles" in label or "лос" in label or "анджелес" in label)
                and ("cdn" in label or "2.0" in label)):
                targets.append(r)
                print(f"\n  🔴 LA CDN: ID={srv_id} {r['country_flag']} {r['label']} (active={r['is_active']})")
            # LTE servers 10 and 12
            if srv_id in (10, 12) or "lte" in label:
                targets.append(r)
                print(f"\n  🔴 LTE: ID={srv_id} {r['country_flag']} {r['label']} (active={r['is_active']})")

        # Check users with keys on these servers
        print("\n" + "=" * 70)
        print("USERS AFFECTED (users with VLESS UUIDs)")
        print("=" * 70)
        user_count = await pool.fetchval(
            "SELECT count(*) FROM user_identities WHERE vless_uuid IS NOT NULL"
        )
        print(f"  Total users with VLESS UUID: {user_count}")

        # Count clients on each target panel
        print("\n" + "=" * 70)
        print("CLIENTS ON TARGET PANELS (will be deleted from panels)")
        print("=" * 70)
        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as client:
            seen_panels = {}
            for r in targets:
                pw = decrypt_field(r["ep"]) if r["ep"] else r["panel_password"]
                panel_url = r["panel_url"].rstrip("/")
                key = panel_url
                if key not in seen_panels:
                    seen_panels[key] = []
                seen_panels[key].append(r)

            for panel_url, srvs in seen_panels.items():
                first = srvs[0]
                pw = decrypt_field(first["ep"]) if first["ep"] else first["panel_password"]
                try:
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
                        json={"username": first["panel_username"], "password": pw}, headers=headers)
                    if login.status_code != 200:
                        print(f"\n  Panel {panel_url}: LOGIN FAILED")
                        continue
                    resp = await client.get(f"{panel_url}/panel/api/inbounds/list", headers=headers)
                    if resp.status_code != 200:
                        print(f"\n  Panel {panel_url}: list failed {resp.status_code}")
                        continue
                    inbounds = resp.json().get("obj", [])
                    for ib in inbounds:
                        settings = ib.get("settings", {})
                        if isinstance(settings, str):
                            settings = json.loads(settings) if settings else {}
                        clients = settings.get("clients", [])
                        print(f"\n  Panel {panel_url}")
                        print(f"    Inbound {ib['id']} port={ib['port']}: {len(clients)} clients")
                        for srv in srvs:
                            if srv["inbound_id"] == ib["id"]:
                                print(f"      → used by server {srv['id']} ({srv['label']})")
                except Exception as e:
                    print(f"\n  Panel {panel_url}: ERROR {e}")

        print("\n" + "=" * 70)
        print("DELETION PLAN (dry-run, nothing deleted yet):")
        print("=" * 70)
        print("1. For each target server:")
        print("   a. Delete VLESS clients from panel inbound (or whole inbound)")
        print("   b. DELETE FROM vpn_servers WHERE id = X")
        print("2. issuance_state NOT affected (no server_id reference)")
        print("3. user_identities.vless_uuid stays (users still have UUIDs)")
        print("\nTarget server IDs to delete from vpn_servers:")
        for r in targets:
            print(f"  - ID={r['id']}: {r['label']}")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
