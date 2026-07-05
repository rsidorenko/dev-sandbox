"""Operator: hard-delete a test user — revoke VLESS keys everywhere + remove all DB rows.

Reuses XuiVlessProvider.delete_user (revokes on xui panels + bare_xray via gRPC), then
hard-deletes every row for the user across the 12 tables that reference internal_user_id /
telegram_user_id, then runs reconcile_all_users (h1 panel rebuilds its client list without the
user; bare_xray orphan removal confirms). Idempotent-ish.

Usage: in production container with DATABASE_URL.
  python delete_test_user.py --telegram-id 123
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, "/app/src")


async def main(tg: int) -> None:
    import asyncpg
    from app.issuance.xui_vless_provider import XuiVlessProvider

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    try:
        row = await pool.fetchrow(
            "SELECT internal_user_id FROM user_identities WHERE telegram_user_id=$1", tg)
        if not row:
            print(f"tg={tg} not found in user_identities — nothing to delete")
            return
        uid = row["internal_user_id"]
        print(f"=== deleting tg={tg} internal_user_id={uid} ===")

        # 1) revoke VLESS keys on all xui panels + bare_xray (tested provider path)
        vp = XuiVlessProvider(pool)
        res = await vp.delete_user(internal_user_id=uid)
        print(f"delete_user (xui+bare_xray) outcome={res.outcome}")
        await vp.aclose()

        # 2) hard-delete all DB rows (FK-safe: children first, user_identities last)
        async with pool.acquire() as conn:
            async with conn.transaction():
                for stmt in (
                    "DELETE FROM billing_subscription_apply_audit_events WHERE internal_user_id=$1",
                    "DELETE FROM billing_subscription_apply_records WHERE internal_user_id=$1",
                    "DELETE FROM billing_events_ledger WHERE internal_user_id=$1",
                    "DELETE FROM issuance_state WHERE internal_user_id=$1",
                    "DELETE FROM notification_log WHERE internal_user_id=$1",
                    "DELETE FROM referral_transactions WHERE internal_user_id=$1",
                    "DELETE FROM referral_balances WHERE internal_user_id=$1",
                    "DELETE FROM referral_codes WHERE internal_user_id=$1",
                    "DELETE FROM referral_relationships WHERE referrer_user_id=$1 OR referred_user_id=$1",
                    "DELETE FROM email_verification_codes WHERE telegram_user_id=$1",
                    "DELETE FROM user_emails WHERE telegram_user_id=$1",
                    "DELETE FROM subscription_snapshots WHERE internal_user_id=$1",
                    "DELETE FROM user_identities WHERE telegram_user_id=$1",
                ):
                    arg = tg if "telegram_user_id" in stmt else uid
                    await conn.execute(stmt, arg)
        print("DB rows deleted (12 tables)")

        # 3) reconcile: h1 rebuilds without the user; bare_xray orphan cleanup confirms
        vp2 = XuiVlessProvider(pool)
        added, failed, total = await vp2.reconcile_all_users()
        print(f"reconcile: added={added} failed={failed} total={total} (h1 rebuilt, bare_xray orphans removed)")
        await vp2.aclose()

        # 4) verify
        n = await pool.fetchval("SELECT count(*) FROM user_identities WHERE telegram_user_id=$1", tg)
        print(f"VERIFY: tg={tg} remaining in user_identities = {n}  ({'OK' if n == 0 else 'STILL PRESENT'})")
    finally:
        await pool.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--telegram-id", type=int, required=True)
    asyncio.run(main(p.parse_args().telegram_id))
