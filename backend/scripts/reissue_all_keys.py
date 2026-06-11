"""One-off operator script: reissue VLESS keys for all active users.

Clears vless_uuid for every user with an active key, then revokes old
keys on all 3x-ui panels and creates fresh ones with flow="" (no
xtls-rprx-vision).

Usage (inside backend container):
    python scripts/reissue_all_keys.py --dry-run   # preview only
    python scripts/reissue_all_keys.py              # live reissue
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg


async def _get_active_users(pool: asyncpg.Pool) -> list[str]:
    """Get internal_user_ids of all users with a VLESS UUID (have keys)."""
    rows = await pool.fetch(
        "SELECT internal_user_id FROM user_identities WHERE vless_uuid IS NOT NULL"
    )
    return [r["internal_user_id"] for r in rows]


async def _reissue_user(pool: asyncpg.Pool, internal_user_id: str) -> str:
    """Reissue keys for a single user: clear UUID → revoke → create."""
    from app.issuance.xui_vless_provider import XuiVlessProvider

    provider = XuiVlessProvider(pool)
    from app.issuance.vless_provider import VlessProviderOutcome

    # 1. Clear stored UUID so reissue generates a fresh one
    await pool.execute(
        "UPDATE user_identities SET vless_uuid = NULL WHERE internal_user_id = $1",
        internal_user_id,
    )

    # 2. Revoke (disable) old keys on all panels
    revoke_result = await provider.revoke_user(internal_user_id=internal_user_id)
    if revoke_result.outcome != VlessProviderOutcome.SUCCESS:
        return f"revoke: {revoke_result.outcome}"

    # 3. Create fresh keys (with flow="" now in xui_client.py)
    create_result = await provider.create_user(internal_user_id=internal_user_id)
    if create_result.outcome != VlessProviderOutcome.SUCCESS:
        return f"create: {create_result.outcome}"

    server_count = len(create_result.config.servers) if create_result.config else 0
    return f"ok ({server_count} servers)"


async def run(dry_run: bool) -> None:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        user_ids = await _get_active_users(pool)
        print(f"Found {len(user_ids)} users with VLESS keys")

        if dry_run:
            for uid in user_ids:
                print(f"  [dry-run] would reissue: {uid[:16]}...")
            print(f"\nDry run complete. {len(user_ids)} users would be reissued.")
            return

        success = 0
        failed = 0
        for i, uid in enumerate(user_ids, 1):
            try:
                result = await _reissue_user(pool, uid)
                if result.startswith("ok"):
                    success += 1
                    print(f"  [{i}/{len(user_ids)}] {uid[:16]}... → {result}")
                else:
                    failed += 1
                    print(f"  [{i}/{len(user_ids)}] {uid[:16]}... → FAILED: {result}")
            except Exception as exc:
                failed += 1
                print(f"  [{i}/{len(user_ids)}] {uid[:16]}... → ERROR: {type(exc).__name__}")

        print(f"\nDone: {success} reissued, {failed} failed out of {len(user_ids)} users")
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
