"""Delete VPN servers 8 (LA 2.0), 10 (LTE), 12 (LTE CDN) — fully.

Safety: checks inbound sharing before deleting panel inbounds.
- DB row: always deleted for targets
- Panel inbound: deleted ONLY if no surviving server uses the same (panel_url, inbound_id)
- Panel clients: deleted from inbounds being removed
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

import httpx

TARGET_IDS = (8, 10, 12)


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


async def run():
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field

        all_rows = await pool.fetch(
            "SELECT id, label, server_host, panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password, '') AS ep, inbound_id, is_active "
            "FROM vpn_servers ORDER BY id"
        )

        targets = [r for r in all_rows if r["id"] in TARGET_IDS]
        survivors = [r for r in all_rows if r["id"] not in TARGET_IDS]

        print("=" * 70)
        print(f"DELETION TARGETS: {[r['id'] for r in targets]}")
        print(f"SURVIVING SERVERS: {[r['id'] for r in survivors]}")
        print("=" * 70)

        # Show inbound_id of each target
        print("\n--- Target inbound_ids ---")
        for r in targets:
            print(f"  ID={r['id']} {r['label']}: panel={r['panel_url']} inbound_id={r['inbound_id']}")

        # Determine which (panel_url, inbound_id) pairs are safe to delete from panels
        # A pair is safe if NO surviving server uses it
        survivor_keys = set()
        for r in survivors:
            survivor_keys.add((r["panel_url"].rstrip("/"), r["inbound_id"]))

        print("\n--- Inbound sharing analysis ---")
        safe_inbounds = {}  # (panel_url, inbound_id) -> list of target rows
        for r in targets:
            panel = r["panel_url"].rstrip("/")
            key = (panel, r["inbound_id"])
            shared = key in survivor_keys
            status = "SHARED (keep inbound, delete DB row only)" if shared else "UNIQUE (safe to delete inbound)"
            print(f"  ID={r['id']} inbound {r['inbound_id']} on {panel}: {status}")
            if not shared:
                safe_inbounds.setdefault(key, []).append(r)

        # ── Phase 1: Delete clients + inbounds from panels (only unique ones) ──
        print("\n" + "=" * 70)
        print("PHASE 1: Panel cleanup (unique inbounds only)")
        print("=" * 70)

        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=20) as client:
            # Group safe inbounds by panel
            panels = {}
            for (panel, ib_id), rows in safe_inbounds.items():
                panels.setdefault(panel, set()).add(ib_id)

            for panel_url, ib_ids in panels.items():
                # Find panel creds from any target row using it
                cred_row = next(r for r in targets if r["panel_url"].rstrip("/") == panel_url)
                pw = decrypt_field(cred_row["ep"]) if cred_row["ep"] else cred_row["panel_password"]
                try:
                    headers = await _panel_login(client, panel_url, cred_row["panel_username"], pw)
                except Exception as e:
                    print(f"\n  Panel {panel_url}: LOGIN FAILED - {e}")
                    continue

                for ib_id in ib_ids:
                    # Delete the whole inbound (3x-ui delInbound deletes all its clients)
                    print(f"\n  Deleting inbound {ib_id} from {panel_url}...")
                    resp = await client.post(
                        f"{panel_url}/panel/api/inbounds/delInbound",
                        headers=headers,
                        json={"id": ib_id}
                    )
                    ok = resp.status_code == 200 and resp.json().get("success", False)
                    print(f"    delInbound {ib_id}: {resp.status_code} success={ok}")
                    if not ok:
                        # Fallback: try removing clients individually via update
                        print(f"    Trying client-level cleanup...")
                        resp2 = await client.get(
                            f"{panel_url}/panel/api/inbounds/get/{ib_id}", headers=headers)
                        if resp2.status_code == 200:
                            ib = resp2.json().get("obj", {})
                            settings = ib.get("settings", "{}")
                            if isinstance(settings, str):
                                settings = json.loads(settings)
                            settings["clients"] = []
                            resp3 = await client.post(
                                f"{panel_url}/panel/api/inbounds/update/{ib_id}",
                                headers=headers,
                                json={"settings": json.dumps(settings)}
                            )
                            print(f"    Cleared clients: {resp3.status_code}")

        # ── Phase 2: Delete DB rows ──
        print("\n" + "=" * 70)
        print("PHASE 2: Delete DB rows")
        print("=" * 70)
        for tid in TARGET_IDS:
            result = await pool.execute("DELETE FROM vpn_servers WHERE id = $1", tid)
            print(f"  DELETE id={tid}: {result}")

        # ── Verify ──
        print("\n" + "=" * 70)
        print("VERIFICATION")
        print("=" * 70)
        remaining = await pool.fetch("SELECT id, label FROM vpn_servers ORDER BY id")
        print("Remaining servers:")
        for r in remaining:
            print(f"  ID={r['id']}: {r['label']}")

        deleted_check = await pool.fetchval(
            "SELECT count(*) FROM vpn_servers WHERE id = ANY($1::int[])", list(TARGET_IDS))
        print(f"\nTargets still in DB: {deleted_check} (should be 0)")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
