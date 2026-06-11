"""One-off fix: re-create VLESS clients on LTE servers (10, 12) only.

The reissue script failed on LTE servers due to panel API errors.
This script re-creates clients on those specific servers using the
correct UUIDs from the DB.

Usage (inside backend container):
    python scripts/fix_lte_servers.py
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg


async def run() -> None:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        from app.issuance.xui_client import XuiApiClient, XuiOutcome
        from app.issuance.xui_vless_provider import (
            _load_server_configs,
            _email_from_internal,
            _expiry_timestamp,
        )

        rows = await pool.fetch(
            "SELECT internal_user_id, vless_uuid FROM user_identities WHERE vless_uuid IS NOT NULL"
        )
        print(f"Found {len(rows)} users")

        configs = await _load_server_configs(pool)
        lte_configs = [c for c in configs if c.server_id in (10, 12)]
        for c in lte_configs:
            print(f"  Target: id={c.server_id} {c.label} ({c.panel_url})")

        if not lte_configs:
            print("No LTE servers found!")
            return

        success = 0
        failed = 0
        for i, row in enumerate(rows, 1):
            uid = row["internal_user_id"]
            uuid = row["vless_uuid"]
            user_ok = 0
            for config in lte_configs:
                client = XuiApiClient(config)
                email = _email_from_internal(uid, transport_type=config.transport_type)
                expiry = _expiry_timestamp()
                try:
                    result = await client.add_client(
                        user_uuid=uuid, email=email,
                        expiry_ts=expiry, enable=True, limit_ip=0,
                    )
                    if result.outcome == XuiOutcome.SUCCESS:
                        user_ok += 1
                        print(f"  [{i}/{len(rows)}] {uid[:16]}... -> {config.label}: OK")
                    elif result.outcome == XuiOutcome.CONFLICT:
                        r2 = await client.update_client(
                            user_uuid=uuid, email=email,
                            enable=True, expiry_ts=expiry, limit_ip=0,
                        )
                        status = "UPDATED" if r2.outcome == XuiOutcome.SUCCESS else f"UPDATE_FAIL({r2.outcome})"
                        if r2.outcome == XuiOutcome.SUCCESS:
                            user_ok += 1
                        print(f"  [{i}/{len(rows)}] {uid[:16]}... -> {config.label}: {status}")
                    else:
                        print(f"  [{i}/{len(rows)}] {uid[:16]}... -> {config.label}: FAIL({result.outcome})")
                except Exception as exc:
                    print(f"  [{i}/{len(rows)}] {uid[:16]}... -> {config.label}: ERROR {type(exc).__name__}: {exc}")

            if user_ok > 0:
                success += 1
            else:
                failed += 1

        print(f"\nDone: {success} ok, {failed} failed out of {len(rows)} users")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
