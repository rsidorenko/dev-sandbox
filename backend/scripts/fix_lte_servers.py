"""Fix LTE servers: re-create clients via read-modify-write.

The standard addClient API returns error on the LTE panel (v3.2.8).
This script uses read-modify-write (same as cleanup) to add/update clients.

Usage: python scripts/fix_lte_servers.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

import asyncpg
import httpx


async def _get_correct_uuids(pool: asyncpg.Pool) -> dict[str, str]:
    rows = await pool.fetch(
        "SELECT internal_user_id, vless_uuid FROM user_identities WHERE vless_uuid IS NOT NULL"
    )
    return {r["internal_user_id"]: r["vless_uuid"] for r in rows}


def _email_to_uid_prefix(email: str) -> str | None:
    m = re.match(r"^(?:x-|cdn-)?user-(.+)$", email)
    return m.group(1) if m else None


async def _fix_panel(
    panel_url: str, username: str, password: str,
    inbound_id: int, correct_uuids: dict[str, str],
    transport_prefix: str,
) -> int:
    """Fix one inbound via read-modify-write. Returns count of updated clients."""
    panel_url = panel_url.rstrip("/")
    async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as client:
        # Login with CSRF
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
            print(f"    LOGIN FAILED: {login.status_code}")
            return 0
        print(f"    Login OK")

        # Read inbound
        resp = await client.get(f"{panel_url}/panel/api/inbounds/get/{inbound_id}", headers=headers)
        if resp.status_code != 200:
            print(f"    GET inbound {inbound_id} FAILED: {resp.status_code}")
            return 0

        ib = resp.json().get("obj", {})
        settings = ib.get("settings", {})
        if isinstance(settings, str):
            settings = json.loads(settings)
        clients = settings.get("clients", [])
        print(f"    Inbound {inbound_id}: {len(clients)} existing clients")

        # Diagnostics: show Reality/stream settings
        stream = ib.get("streamSettings", {})
        net = stream.get("network", "?")
        sec = stream.get("security", "?")
        print(f"    Stream: network={net} security={sec}")
        if "realitySettings" in stream:
            rs = stream["realitySettings"]
            print(f"    Reality: dest={str(rs.get('dest',''))[:50]} serverNames={rs.get('serverNames',[])} shortIds={rs.get('shortIds',[])}")
        ws = stream.get("wssettings", stream.get("wsSettings", {}))
        if ws:
            print(f"    WS: path={ws.get('path','?')} host={ws.get('headers',{}).get('Host','?')}")
        # Show first client details
        if clients:
            c0 = clients[0]
            print(f"    Client[0]: email={c0.get('email','')} flow=\"{c0.get('flow','')}\" uuid={c0.get('id','')[:12]}...")

        # Build email -> client map
        by_email = {c.get("email", ""): c for c in clients}

        updated = 0
        for uid, uuid in correct_uuids.items():
            email = f"{transport_prefix}user-{uid[:16]}"
            if email in by_email:
                # Update existing client's UUID and flow
                by_email[email]["id"] = uuid
                by_email[email]["flow"] = ""
                by_email[email]["enable"] = True
                updated += 1
            else:
                # Add new client
                by_email[email] = {
                    "id": uuid,
                    "email": email,
                    "enable": True,
                    "expiryTime": 0,
                    "flow": "",
                    "limitIp": 0,
                    "totalGB": 0,
                    "tgId": "",
                    "subId": "",
                }
                updated += 1

        # Write back
        settings["clients"] = list(by_email.values())
        ib["settings"] = json.dumps(settings)
        update_payload = {k: v for k, v in ib.items()
                        if k not in ("clientStats", "up", "down", "total")}
        update_payload["settings"] = json.dumps(settings)

        resp = await client.post(
            f"{panel_url}/panel/api/inbounds/update/{inbound_id}",
            json=update_payload, headers=headers,
        )
        ok = "OK" if resp.status_code == 200 else f"FAIL({resp.status_code})"
        print(f"    Update inbound {inbound_id}: {ok} ({len(by_email)} clients, {updated} updated)")

        # Restart xray
        resp = await client.post(f"{panel_url}/panel/api/setting/restartXrayService", headers=headers)
        ok = "OK" if resp.status_code == 200 else f"FAIL({resp.status_code})"
        print(f"    Restart xray: {ok}")

        return updated


async def run() -> None:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    from app.security.field_encryption import decrypt_field

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        correct_uuids = await _get_correct_uuids(pool)
        print(f"Found {len(correct_uuids)} users")

        rows = await pool.fetch(
            "SELECT id, label, panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password, '') AS encrypted_password, "
            "inbound_id, transport_type "
            "FROM vpn_servers WHERE is_active = TRUE AND id IN (10, 12) ORDER BY id"
        )

        for row in rows:
            password = row["encrypted_password"] if row["encrypted_password"] else row["panel_password"]
            if row["encrypted_password"]:
                password = decrypt_field(row["encrypted_password"])

            transport_type = row["transport_type"]
            prefix = {"xhttp": "x-", "cdn": "cdn-"}.get(transport_type, "")
            inbound_id = row["inbound_id"]

            print(f"\nServer {row['id']}: {row['label']}")
            print(f"  Panel: {row['panel_url']}, inbound={inbound_id}, transport={transport_type}")
            count = await _fix_panel(
                row["panel_url"], row["panel_username"], password,
                inbound_id, correct_uuids, prefix,
            )
            print(f"  Result: {count} clients updated")

        print(f"\nDone!")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
