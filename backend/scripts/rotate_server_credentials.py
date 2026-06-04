"""Rotate credentials for a VPN server entry.

Encrypts the new password and clears plaintext. Use after credential leak
or periodic rotation.

Usage:
  DATABASE_URL=... FIELD_ENCRYPTION_KEY=<key> \
  ROTATE_PANEL_USERNAME=<user> ROTATE_PANEL_PASSWORD=<pass> ROTATE_API_TOKEN=<token> \
  python scripts/rotate_server_credentials.py <server_id>

  --dry-run: show what would change without modifying the database.
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

from app.security.field_encryption import encrypt_field


async def main() -> None:
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage: rotate_server_credentials.py <server_id> [--dry-run]", file=sys.stderr)
        sys.exit(1)

    server_id = int(args[0])

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL is required", file=sys.stderr)
        sys.exit(1)

    key = os.environ.get("FIELD_ENCRYPTION_KEY", "").strip()
    if not key:
        print("FIELD_ENCRYPTION_KEY is required", file=sys.stderr)
        sys.exit(1)

    new_username = os.environ.get("ROTATE_PANEL_USERNAME", "").strip()
    new_password = os.environ.get("ROTATE_PANEL_PASSWORD", "").strip()
    new_token = os.environ.get("ROTATE_API_TOKEN", "").strip()

    if not any([new_username, new_password, new_token]):
        print("At least one of ROTATE_PANEL_USERNAME, ROTATE_PANEL_PASSWORD, ROTATE_API_TOKEN is required", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT id, label, panel_username, panel_url FROM vpn_servers WHERE id = $1",
            server_id,
        )
        if row is None:
            print(f"Server id={server_id} not found.", file=sys.stderr)
            sys.exit(1)

        print(f"Rotating credentials for [{server_id}] {row['label']} ({row['panel_url']})")

        updates: list[str] = []
        params: list[str | int] = []
        idx = 1

        if new_password:
            encrypted = encrypt_field(new_password)
            idx += 1
            updates.append(f"encrypted_password = ${idx}")
            params.append(encrypted)
            idx += 1
            updates.append(f"panel_password = ${idx}")
            params.append("")

        if new_username:
            idx += 1
            updates.append(f"panel_username = ${idx}")
            params.append(new_username)

        if new_token:
            idx += 1
            updates.append(f"api_token = ${idx}")
            params.append(new_token)

        idx += 1
        params.append(server_id)

        sql = f"UPDATE vpn_servers SET {', '.join(updates)} WHERE id = ${idx}"

        if dry_run:
            print(f"  Would execute: {sql}")
            print(f"  Fields to update: {', '.join(u.split('=')[0].strip() for u in updates)}")
        else:
            await conn.execute(sql, *params)
            print(f"  Credentials rotated successfully.")

        print("After rotation, verify the new credentials work with the 3x-ui panel.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
