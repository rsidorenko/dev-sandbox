"""Encrypt panel_password values in vpn_servers table.

Run: FIELD_ENCRYPTION_KEY=<base64-key> python scripts/encrypt_panel_passwords.py

Generates a key if none provided.
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys

import asyncpg

from app.security.field_encryption import encrypt_field


async def main() -> None:
    key = os.environ.get("FIELD_ENCRYPTION_KEY", "").strip()
    if not key:
        key_bytes = __import__("secrets").token_bytes(32)
        key = base64.b64encode(key_bytes).decode("ascii")
        print(f"Generated new key (save this!): FIELD_ENCRYPTION_KEY={key}")
        os.environ["FIELD_ENCRYPTION_KEY"] = key

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL is required", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT id, panel_password, COALESCE(encrypted_password, '') AS encrypted_password FROM vpn_servers")
        if not rows:
            print("No vpn_servers found.")
            return

        for row in rows:
            sid = row["id"]
            existing_enc = row["encrypted_password"]
            plain = row["panel_password"]

            if existing_enc:
                print(f"  server {sid}: already encrypted, skipping")
                continue

            if not plain:
                print(f"  server {sid}: empty password, skipping")
                continue

            encrypted = encrypt_field(plain)
            await conn.execute(
                "UPDATE vpn_servers SET encrypted_password = $1 WHERE id = $2",
                encrypted,
                sid,
            )
            print(f"  server {sid}: encrypted password")

        print("Done. Verify with: SELECT id, encrypted_password FROM vpn_servers;")
        print("After verification, clear plaintext: UPDATE vpn_servers SET panel_password = '';")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
