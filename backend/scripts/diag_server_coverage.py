#!/usr/bin/env python3
"""Operator (READ-ONLY): why is a server missing from some users' /sub/ list?

The subscription list (/sub/<token> via get_user_config) shows ONLY the servers
where the user's client exists in the inbound's settings.clients JSON
(resolve_client_uuid -> _resolve_client_uuid_v3 reads that JSON). So a server is
hidden from a user when their client is absent from the panel inbound JSON.

This diagnostic:
  1. dumps EVERY vpn_servers row (active + inactive) — answers "does LA 2.0 /
     Russia exist, and is it active?";
  2. for each ACTIVE server, fetches the inbound settings.clients JSON and
     reports how many ACTIVE subscribed users are present vs MISSING there
     (i.e. won't see this server in their /sub/);
  3. lists the internal_user_ids / telegram ids that are MISSING on each server,
     capped (--sample).

Run in the production container:
  python diag_server_coverage.py [--sample 20]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import asyncpg

sys.path.insert(0, os.path.dirname(__file__))
from rebuild_panel_clients import PanelClient  # noqa: E402


def _userid_from_email(email: str) -> str | None:
    """Reverse _email_from_internal: 'x-user-<id[:16]>' / 'cdn-user-..' / 'user-..' -> <id[:16]>."""
    e = email or ""
    for pre in ("x-user-", "cdn-user-", "user-"):
        if e.startswith(pre):
            return e[len(pre):]
    return None


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sample", type=int, default=20, help="how many missing ids to print per server")
    args = p.parse_args()

    from app.security.field_encryption import decrypt_field

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    try:
        # 1) ALL servers (active + inactive), with creds resolved
        rows = await pool.fetch(
            """SELECT id, label, country_code, server_host, server_port,
                      COALESCE(transport_type,'tcp') AS transport_type,
                      is_active, COALESCE(reality_sni,'') AS reality_sni,
                      COALESCE(tls_sni,'') AS tls_sni, inbound_id,
                      panel_url, panel_username,
                      COALESCE(encrypted_password,'') AS encrypted_password,
                      COALESCE(panel_password,'') AS panel_password
               FROM vpn_servers ORDER BY id"""
        )
        servers = []
        print("=" * 80)
        print("ALL vpn_servers rows (active + inactive)")
        print("=" * 80)
        print(f"{'id':>3} {'act':>3} {'transport':>9} {'host:port':>22}  label")
        for r in rows:
            hostport = f"{r['server_host']}:{r['server_port']}"
            print(f"{r['id']:>3} {'Y' if r['is_active'] else '-':>3} {r['transport_type']:>9} {hostport:>22}  "
                  f"{r['country_code']} {r['label']}  (sni={r['reality_sni'] or r['tls_sni'] or '-'})")
            pw = decrypt_field(r["encrypted_password"]) if r["encrypted_password"] else r["panel_password"]
            servers.append({
                "id": r["id"], "label": r["label"], "cc": r["country_code"],
                "host": r["server_host"], "port": r["server_port"], "ttype": r["transport_type"],
                "active": r["is_active"], "panel_url": r["panel_url"], "panel_username": r["panel_username"],
                "panel_password": pw, "inbound_id": r["inbound_id"],
            })
        print(f"\ntotal rows: {len(rows)} | active: {sum(1 for s in servers if s['active'])}")

        # 2) active users
        users = await pool.fetch(
            """SELECT i.internal_user_id, i.telegram_user_id
               FROM user_identities i
               JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id
               WHERE s.state_label = 'active' AND i.vless_uuid IS NOT NULL"""
        )
        active_ids = {u["internal_user_id"]: u["telegram_user_id"] for u in users}
        print(f"active subscribed users (with vless_uuid): {len(active_ids)}")

        # 3) per active server, JSON coverage
        print("\n" + "=" * 80)
        print("Per ACTIVE server: which active users are PRESENT in settings.clients JSON")
        print("=" * 80)
        for s in servers:
            if not s["active"]:
                continue
            tag = f"id={s['id']} {s['cc']} {s['label']} [{s['ttype']}] {s['host']}:{s['port']}"
            pc = PanelClient(
                panel_url=s["panel_url"], username=s["panel_username"], password=s["panel_password"],
                inbound_id=s["inbound_id"], server_id=s["id"], label=s["label"],
                transport_type=s["ttype"], server_host=s["host"], server_port=s["port"],
            )
            inbound = await pc.get_inbound()
            await pc.aclose()
            if not inbound:
                print(f"\n--- {tag}\n    !! could not fetch inbound (login/list failed) — server hidden for everyone")
                continue
            settings = inbound.get("settings", {})
            if isinstance(settings, str):
                try:
                    settings = json.loads(settings)
                except Exception:
                    settings = {}
            present_ids = {_userid_from_email(c.get("email", "")) for c in settings.get("clients", [])}
            present_ids.discard(None)
            active_short = {uid[:16]: uid for uid in active_ids}
            present_active = [uid for sh, uid in active_short.items() if sh in present_ids]
            missing_active = [uid for sh, uid in active_short.items() if sh not in present_ids]
            print(f"\n--- {tag}")
            print(f"    JSON clients total   : {len(present_ids)}")
            print(f"    active users PRESENT : {len(present_active)} / {len(active_ids)}")
            print(f"    active users MISSING : {len(missing_active)}  <- these will NOT see this server in /sub/")
            if missing_active:
                shown = missing_active[: args.sample]
                sample_txt = ", ".join(f"{uid[:10]}/tg={active_ids[uid]}" for uid in shown)
                print(f"    sample missing: {sample_txt}{' ...' if len(missing_active) > args.sample else ''}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
