"""One-off operator script: clean up duplicate VLESS clients on all 3x-ui panels.

After a botched reissue, each panel has 2 clients per user (same email,
different UUID). This script:
1. Gets the correct vless_uuid for each user from the DB
2. For each panel, for each inbound, reads full inbound config
3. Filters clients: keeps only the one whose UUID matches DB
4. Writes back the cleaned inbound config (read-modify-write)
5. Restarts xray on each server

Usage (inside backend container):
    python scripts/cleanup_duplicate_clients.py --dry-run   # preview only
    python scripts/cleanup_duplicate_clients.py              # live cleanup
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import defaultdict

import asyncpg
import httpx


async def _get_correct_uuids(pool: asyncpg.Pool) -> dict[str, str]:
    """Get internal_user_id -> vless_uuid mapping."""
    rows = await pool.fetch(
        "SELECT internal_user_id, vless_uuid FROM user_identities WHERE vless_uuid IS NOT NULL"
    )
    return {r["internal_user_id"]: r["vless_uuid"] for r in rows}


def _email_to_user_id_prefix(email: str) -> str | None:
    """Extract internal_user_id prefix from 3x-ui client email."""
    m = re.match(r"^(?:x-|cdn-)?user-(.+)$", email)
    return m.group(1) if m else None


async def _cleanup_panel(
    panel_url: str,
    username: str,
    password: str,
    correct_uuids: dict[str, str],
    dry_run: bool,
) -> tuple[int, int]:
    """Clean up one panel via read-modify-write. Returns (kept, deleted)."""
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
            print(f"  LOGIN FAILED: {login.status_code}")
            return 0, 0

        # Get all inbounds
        list_resp = await client.get(f"{panel_url}/panel/api/inbounds/list", headers=headers)
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

            # Group by email to find duplicates
            by_email: dict[str, list[dict]] = defaultdict(list)
            for c in clients:
                by_email[c.get("email", "")].append(c)

            duplicates = {e: cs for e, cs in by_email.items() if len(cs) > 1}
            if not duplicates:
                total_kept += len(clients)
                continue

            print(f"  Inbound {inbound_id}: {len(clients)} clients, {len(duplicates)} dupes")

            # Build cleaned client list: for duplicates, keep only correct UUID
            cleaned = []
            for email, dupes in by_email.items():
                if len(dupes) == 1:
                    cleaned.extend(dupes)
                    continue
                # Find correct UUID
                prefix = _email_to_user_id_prefix(email)
                correct = None
                if prefix:
                    for uid, uuid in correct_uuids.items():
                        if uid.startswith(prefix):
                            correct = uuid
                            break
                # Keep correct, delete rest
                kept_one = False
                for c in dupes:
                    c_uuid = c.get("id", "")
                    if correct and c_uuid == correct:
                        cleaned.append(c)
                        kept_one = True
                    elif not kept_one and (not correct or c_uuid != correct):
                        # If we can't determine correct, keep last one (newest)
                        pass
                    if dry_run:
                        action = "keep" if (correct and c_uuid == correct) else "remove"
                        print(f"    [dry-run] {action}: {email} uuid={c_uuid[:8]}...")
                    else:
                        if correct and c_uuid != correct:
                            print(f"    remove: {email} uuid={c_uuid[:8]}...")
                            total_deleted += 1
                        else:
                            print(f"    keep: {email} uuid={c_uuid[:8]}...")
                # If none matched correct UUID, keep last one
                if not kept_one:
                    cleaned.append(dupes[-1])
                    if not dry_run:
                        total_deleted += len(dupes) - 1

            if dry_run:
                print(f"    -> would go from {len(clients)} to {len(cleaned)} clients")
                continue

            # Write back cleaned settings via update inbound
            settings["clients"] = cleaned
            ib["settings"] = json.dumps(settings)
            # Remove fields that shouldn't be in update payload
            update_payload = {k: v for k, v in ib.items()
                            if k not in ("clientStats", "up", "down", "total")}
            update_payload["settings"] = json.dumps(settings)

            resp = await client.post(
                f"{panel_url}/panel/api/inbounds/update/{inbound_id}",
                json=update_payload, headers=headers,
            )
            ok = "OK" if resp.status_code == 200 else f"FAIL({resp.status_code})"
            print(f"    update inbound {inbound_id}: {ok} ({len(cleaned)} clients)")
            total_kept += len(cleaned)

        # Restart xray on this panel
        if not dry_run:
            restart = await client.post(
                f"{panel_url}/panel/api/setting/restartXrayService", headers=headers)
            ok = "OK" if restart.status_code == 200 else f"FAIL({restart.status_code})"
            print(f"  Restart xray: {ok}")

        return total_kept, total_deleted


async def run(dry_run: bool) -> None:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    from app.security.field_encryption import decrypt_field

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        correct_uuids = await _get_correct_uuids(pool)
        print(f"Found {len(correct_uuids)} users with correct UUIDs in DB")

        rows = await pool.fetch(
            "SELECT id, label, panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password, '') AS encrypted_password "
            "FROM vpn_servers WHERE is_active = TRUE ORDER BY id"
        )

        grand_kept = 0
        grand_deleted = 0
        for row in rows:
            password = row["encrypted_password"] if row["encrypted_password"] else row["panel_password"]
            if row["encrypted_password"]:
                password = decrypt_field(row["encrypted_password"])

            print(f"\nServer {row['id']}: {row['label']} ({row['panel_url']})")
            kept, deleted = await _cleanup_panel(
                row["panel_url"], row["panel_username"], password,
                correct_uuids, dry_run,
            )
            grand_kept += kept
            grand_deleted += deleted

        print(f"\n{'[DRY RUN] ' if dry_run else ''}Total: {grand_kept} kept, {grand_deleted} deleted")
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
