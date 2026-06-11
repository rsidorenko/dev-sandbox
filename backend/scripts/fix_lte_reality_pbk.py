"""Fix LTE reality_pbk mismatch: sync DB to actual server Reality key.

The 3x-ui DB regeneration created a NEW Reality key pair in the inbound,
but vpn_servers.reality_pbk still has the OLD public key. This script:
1. Reads the actual privateKey from 3x-ui inbound
2. Derives the matching publicKey
3. Updates vpn_servers.reality_pbk (and reality_sid if needed)
"""

import asyncio
import json
import os
import re
import sys

import httpx


async def run():
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field

        # Get LTE server config from DB
        row = await pool.fetchrow(
            "SELECT id, panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password, '') AS ep, "
            "inbound_id, reality_pbk, reality_sid "
            "FROM vpn_servers WHERE id = 10"
        )
        pw = decrypt_field(row["ep"]) if row["ep"] else row["panel_password"]
        panel_url = row["panel_url"].rstrip("/")
        inbound_id = row["inbound_id"]
        db_pbk = row["reality_pbk"]
        db_sid = row["reality_sid"]

        print(f"DB reality_pbk: {db_pbk}")
        print(f"DB reality_sid: {db_sid}")

        # Get actual Reality settings from panel
        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as client:
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
                json={"username": row["panel_username"], "password": pw}, headers=headers)
            if login.status_code != 200:
                print(f"Login FAILED: {login.status_code}")
                return

            resp = await client.get(f"{panel_url}/panel/api/inbounds/get/{inbound_id}", headers=headers)
            if resp.status_code != 200:
                print(f"Get inbound FAILED: {resp.status_code}")
                return

            ib = resp.json().get("obj", {})
            stream = ib.get("streamSettings", {})
            rs = stream.get("realitySettings", {})
            actual_privkey = rs.get("privateKey", "")
            actual_sids = rs.get("shortIds", [])
            actual_snis = rs.get("serverNames", [])
            actual_dest = rs.get("dest", "")

            print(f"\nPanel privateKey: {actual_privkey}")
            print(f"Panel shortIds: {actual_sids}")
            print(f"Panel serverNames: {actual_snis}")
            print(f"Panel dest: {actual_dest}")

            if not actual_privkey:
                print("ERROR: no privateKey in panel Reality settings!")
                return

            # Derive public key from private key using x25519
            try:
                from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
                from cryptography.hazmat.primitives import serialization
                import base64

                priv_bytes = base64.urlsafe_b64decode(actual_privkey + "==")
                priv_key = X25519PrivateKey.from_private_bytes(priv_bytes)
                pub_bytes = priv_key.public_key().public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw
                )
                actual_pubkey = base64.urlsafe_b64encode(pub_bytes).decode().rstrip("=")
                print(f"\nDerived publicKey: {actual_pubkey}")
            except Exception as e:
                print(f"ERROR deriving pubkey: {e}")
                return

            # Compare with DB
            if db_pbk == actual_pubkey:
                print("\nDB pbk already matches derived pubkey. No change needed.")
                return

            # Use first shortId if DB sid is empty
            new_sid = db_sid if db_sid else (actual_sids[0] if actual_sids else "")

            print(f"\nMISMATCH! DB pbk={db_pbk}")
            print(f"          derived  ={actual_pubkey}")
            print(f"\nUpdating DB reality_pbk -> {actual_pubkey}")
            if db_sid != new_sid and new_sid:
                print(f"Updating DB reality_sid -> {new_sid}")

            await pool.execute(
                "UPDATE vpn_servers SET reality_pbk = $1, reality_sid = $2 WHERE id = 10",
                actual_pubkey, new_sid
            )
            print("DB updated! Now regenerate client links via /reissue or key reissue flow.")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
