"""Encrypt all plaintext panel_password values and clear the plaintext column.

Usage:
  DATABASE_URL=... FIELD_ENCRYPTION_KEY=<base64-key> python scripts/migrate_encrypt_passwords.py
  DATABASE_URL=... FIELD_ENCRYPTION_KEY=<base64-key> python scripts/migrate_encrypt_passwords.py --dry-run
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

from app.security.field_encryption import encrypt_field


async def main() -> None:
    dry_run = "--dry-run" in sys.argv

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL is required", file=sys.stderr)
        sys.exit(1)

    key = os.environ.get("FIELD_ENCRYPTION_KEY", "").strip()
    if not key:
        print("FIELD_ENCRYPTION_KEY is required", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, label, panel_password, COALESCE(encrypted_password, '') AS encrypted_password "
            "FROM vpn_servers ORDER BY id"
        )
        if not rows:
            print("No vpn_servers found.")
            return

        encrypted_count = 0
        for row in rows:
            sid = row["id"]
            label = row["label"]
            existing_enc = row["encrypted_password"]
            plain = row["panel_password"]

            if existing_enc and not plain:
                print(f"  [{sid}] {label}: OK (already encrypted, plaintext cleared)")
                continue

            if existing_enc and plain:
                status = "would clear plaintext" if dry_run else "clearing plaintext"
                print(f"  [{sid}] {label}: {status}")
                if not dry_run:
                    await conn.execute(
                        "UPDATE vpn_servers SET panel_password = '' WHERE id = $1",
                        sid,
                    )
                continue

            if not plain:
                print(f"  [{sid}] {label}: WARNING — no password at all (encrypted and plaintext empty)")
                continue

            encrypted = encrypt_field(plain)
            action = "would encrypt" if dry_run else "encrypting"
            print(f"  [{sid}] {label}: {action} password")
            if not dry_run:
                await conn.execute(
                    "UPDATE vpn_servers SET encrypted_password = $1, panel_password = '' WHERE id = $2",
                    encrypted,
                    sid,
                )
            encrypted_count += 1

        suffix = " (dry-run)" if dry_run else ""
        print(f"\nDone{suffix}: {encrypted_count} password(s) {'would be ' if dry_run else ''}encrypted.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
