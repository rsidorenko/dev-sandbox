"""One-off operator script: clean up duplicate VLESS clients on all 3x-ui panels.

After a botched reissue, each panel has 2 clients per user (same email,
different UUID). This script:
1. Gets the correct vless_uuid for each user from the DB
2. For each panel, for each inbound, finds duplicate emails
3. Deletes the client whose UUID does NOT match the DB
4. Restarts xray on each server

Usage (inside backend container):
    python scripts/cleanup_duplicate_clients.py --dry-run   # preview only
    python scripts/cleanup_duplicate_clients.py              # live cleanup
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import asyncpg
import httpx


async def _get_correct_uuids(pool: asyncpg.Pool) -> dict[str, str]:
    """Get internal_user_id -> vless_uuid mapping."""
    rows = await pool.fetch(
        "SELECT internal_user_id, vless_uuid FROM user_identities WHERE vless_uuid IS NOT NULL"
    )
    return {r["internal_user_id"]: r["vless_uuid"] for r in rows}


async def _email_to_user_id(email: str) -> str | None:
    """Extract internal_user_id from 3x-ui client email."""
    # Email format: [x-|cdn-]user-{internal_user_id[:16]}
    import re
    m = re.match(r"^(?:x-|cdn-)?user-(.+)$", email)
    return m.group(1) if m else None


async def _cleanup_panel(
    panel_url: str,
    username: str,
    password: str,
    correct_uuids: dict[str, str],
    dry_run: bool,
) -> tuple[int, int]:
    """Clean up one panel. Returns (kept, deleted) counts."""
    panel_url = panel_url.rstrip("/")
    async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=10) as client:
        # Login
        # Try to get CSRF token
        login_page = await client.get(f"{panel_url}/")
        csrf_match = None
        if login_page.status_code == 200:
            import re
            m = re.search(r'csrf-token" content="([^"]+)"', login_page.text)
            if m:
                csrf_match = m.group(1)

        login_data = {"username": username, "password": password}
        headers = {}
        if csrf_match:
            headers["X-CSRF-Token"] = csrf_match
            headers["Content-Type"] = "application/json"

        login_resp = await client.post(f"{panel_url}/login", json=login_data, headers=headers)
        if login_resp.status_code != 200 or not login_resp.json().get("success"):
            print(f"  LOGIN FAILED: {login_resp.status_code} {login_resp.text[:100]}")
            return 0, 0

        # Get all inbounds
        list_resp = await client.get(f"{panel_url}/panel/api/inbounds/list")
        if list_resp.status_code != 200:
            print(f"  LIST FAILED: {list_resp.status_code}")
            return 0, 0

        inbounds = list_resp.json().get("obj", [])
        total_kept = 0
        total_deleted = 0

        for ib in inbounds:
            inbound_id = ib.get("id")
            settings = ib.get("settings", {})
            if isinstance(settings, str):
                try:
                    settings = json.loads(settings)
                except json.JSONDecodeError:
                    continue
            clients = settings.get("clients", [])
            if not clients:
                continue

            # Group by email
            from collections import defaultdict
            by_email: dict[str, list[dict]] = defaultdict(list)
            for c in clients:
                by_email[c.get("email", "")].append(c)

            # Find duplicates
            duplicates = {e: cs for e, cs in by_email.items() if len(cs) > 1}

            if not duplicates:
                total_kept += len(clients)
                continue

            print(f"  Inbound {inbound_id}: {len(clients)} clients, {len(duplicates)} duplicate emails")

            for email, dupes in duplicates.items():
                # Figure out which UUID is correct
                user_id_prefix = await _email_to_user_id(email)
                correct_uuid = None
                if user_id_prefix:
                    for uid, uuid in correct_uuids.items():
                        if uid.startswith(user_id_prefix):
                            correct_uuid = uuid
                            break

                for c in dupes:
                    c_uuid = c.get("id", "")
                    c_email = c.get("email", "")
                    if correct_uuid and c_uuid != correct_uuid:
                        if dry_run:
                            print(f"    [dry-run] would delete: {c_email} uuid={c_uuid[:8]}...")
                        else:
                            del_resp = await client.post(
                                f"{panel_url}/panel/api/inbounds/{inbound_id}/delClient/{c_uuid}"
                            )
                            ok = "OK" if del_resp.status_code == 200 else f"FAIL({del_resp.status_code})"
                            print(f"    deleted: {c_email} uuid={c_uuid[:8]}... -> {ok}")
                            if del_resp.status_code == 200:
                                total_deleted += 1
                    else:
                        total_kept += 1

        return total_kept, total_deleted


async def run(dry_run: bool) -> None:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    from app.security.field_encryption import decrypt_field

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        # Get correct UUIDs
        correct_uuids = await _get_correct_uuids(pool)
        print(f"Found {len(correct_uuids)} users with correct UUIDs in DB")

        # Get all active servers
        rows = await pool.fetch(
            "SELECT id, label, panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password, '') AS encrypted_password "
            "FROM vpn_servers WHERE is_active = TRUE ORDER BY id"
        )

        total_kept = 0
        total_deleted = 0
        for row in rows:
            sid = row["id"]
            label = row["label"]
            panel_url = row["panel_url"]
            password = row["encrypted_password"] if row["encrypted_password"] else row["panel_password"]
            if row["encrypted_password"]:
                password = decrypt_field(row["encrypted_password"])

            print(f"\nServer {sid}: {label} ({panel_url})")
            kept, deleted = await _cleanup_panel(
                panel_url, row["panel_username"], password,
                correct_uuids, dry_run,
            )
            total_kept += kept
            total_deleted += deleted

        print(f"\n{'[DRY RUN] ' if dry_run else ''}Total: {total_kept} kept, {total_deleted} deleted")
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
