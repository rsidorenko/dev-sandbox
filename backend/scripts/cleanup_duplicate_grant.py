#!/usr/bin/env python3
"""Operator: remove duplicate grant ledger facts for a user, keeping the newest.

grant_subscription.py was run twice for the same user (first run hit an apply/clobber
bug), so two billing_events_ledger 'subscription_activated' facts exist, both applied.
This keeps the NEWEST grant fact and deletes the older duplicate(s) from:
  - billing_subscription_apply_audit_events
  - billing_subscription_apply_records
  - billing_events_ledger
(children first, in case of FKs).

ONLY touches facts whose internal_fact_ref starts with 'grant:' (operator-granted) —
never real payment facts. Dry-run by default; --apply to delete.

Run in container: python cleanup_duplicate_grant.py --telegram-id 8158783115 [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--telegram-id", type=int, required=True)
    p.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    args = p.parse_args()

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        uid = await pool.fetchval(
            "SELECT internal_user_id FROM user_identities WHERE telegram_user_id=$1",
            args.telegram_id,
        )
        if not uid:
            print(f"user telegram_user_id={args.telegram_id} not found")
            return

        facts = await pool.fetch(
            "SELECT internal_fact_ref, event_received_at, amount_minor_units, status, event_type "
            "FROM billing_events_ledger "
            "WHERE internal_user_id=$1 AND internal_fact_ref LIKE 'grant:%' "
            "ORDER BY event_received_at",
            uid,
        )
        print(f"user {uid}: {len(facts)} grant fact(s)")
        for f in facts:
            print(f"  {f['event_received_at']}  {f['internal_fact_ref']}  "
                  f"{f['amount_minor_units']}k  {f['status']}  {f['event_type']}")

        if len(facts) <= 1:
            print("\nnothing to clean (<=1 grant fact)")
            return

        keep = facts[-1]["internal_fact_ref"]
        to_remove = [f["internal_fact_ref"] for f in facts[:-1]]
        print(f"\nKEEP newest: {keep}")
        print(f"REMOVE {len(to_remove)} duplicate(s):")
        for ref in to_remove:
            print(f"  - {ref}")

        if not args.apply:
            print("\n[DRY RUN] re-run with --apply to delete")
            return

        for ref in to_remove:
            a = await pool.execute(
                "DELETE FROM billing_subscription_apply_audit_events WHERE internal_fact_ref=$1", ref)
            b = await pool.execute(
                "DELETE FROM billing_subscription_apply_records WHERE internal_fact_ref=$1", ref)
            c = await pool.execute(
                "DELETE FROM billing_events_ledger WHERE internal_fact_ref=$1", ref)
            print(f"  removed {ref}: audit={a} apply_records={b} ledger={c}")

        snap = await pool.fetchrow(
            "SELECT state_label, plan_id, active_until_utc FROM subscription_snapshots WHERE internal_user_id=$1",
            uid,
        )
        remaining = await pool.fetchval(
            "SELECT count(*) FROM billing_events_ledger WHERE internal_user_id=$1 AND internal_fact_ref LIKE 'grant:%'",
            uid,
        )
        print(f"\nVERIFY: snapshot state={snap['state_label']} plan={snap['plan_id']} "
              f"until={snap['active_until_utc']} | remaining grant facts={remaining}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
