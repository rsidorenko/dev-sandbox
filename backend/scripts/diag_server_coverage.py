#!/usr/bin/env python3
"""Operator (READ-ONLY): who has keys on the main servers but NOT on Russia (id=11)?

/sub/<token> lists only servers where the user's client exists in the inbound
settings.clients JSON. A user missing from a server's JSON won't see it.

The earlier "active users" framing was wrong: panels hold ~23 clients but only
~19 are state_label='active'. The users with keys who are NOT active (trial /
grace / expired) are provisioned on the OLD servers but were never reconciled
onto Russia (id=11, added later; reconcile_all_active_users only covers active).

This diagnostic diffs the panel client sets directly (ground truth of who has a
key), independent of snapshot state:
  1. dumps EVERY vpn_servers row (active + inactive);
  2. for each active server, fetches the inbound settings.clients JSON -> set of
     client user-ids (from email prefix);
  3. union = all users with a key on ANY active server;
  4. per server, reports users in union but MISSING from that server (with
     telegram id + snapshot state) — focused on Russia (id=11).

Run in the production container:
  python diag_server_coverage.py
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
    e = email or ""
    for pre in ("x-user-", "cdn-user-", "user-"):
        if e.startswith(pre):
            return e[len(pre):]
    return None


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    args = p.parse_args()

    from app.security.field_encryption import decrypt_field

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    try:
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
            if r["is_active"]:
                servers.append({
                    "id": r["id"], "label": r["label"], "cc": r["country_code"],
                    "host": r["server_host"], "port": r["server_port"], "ttype": r["transport_type"],
                    "panel_url": r["panel_url"], "panel_username": r["panel_username"],
                    "panel_password": pw, "inbound_id": r["inbound_id"],
                })

        # fetch client sets per active server
        print("\n" + "=" * 80)
        print("Client sets per ACTIVE server (from settings.clients JSON)")
        print("=" * 80)
        client_sets: dict[int, set[str]] = {}
        for s in servers:
            pc = PanelClient(
                panel_url=s["panel_url"], username=s["panel_username"], password=s["panel_password"],
                inbound_id=s["inbound_id"], server_id=s["id"], label=s["label"],
                transport_type=s["ttype"], server_host=s["host"], server_port=s["port"],
            )
            inbound = await pc.get_inbound()
            await pc.aclose()
            tag = f"id={s['id']} {s['cc']} {s['label']} [{s['ttype']}]"
            if not inbound:
                print(f"  {tag:<48} !! inbound fetch FAILED")
                client_sets[s["id"]] = set()
                continue
            settings = inbound.get("settings", {})
            if isinstance(settings, str):
                try:
                    settings = json.loads(settings)
                except Exception:
                    settings = {}
            ids = {_userid_from_email(c.get("email", "")) for c in settings.get("clients", [])}
            ids.discard(None)
            client_sets[s["id"]] = ids
            print(f"  {tag:<48} clients={len(ids)}")

        union = set().union(*client_sets.values()) if client_sets else set()
        print(f"\n  TOTAL distinct users with a key on ANY active server: {len(union)}")

        # telegram ids + snapshot states for everyone in the union
        meta: dict[str, dict] = {}
        if union:
            # internal_user_id prefix is internal_user_id[:16]; match on prefix
            recs = await pool.fetch(
                """SELECT i.internal_user_id, i.telegram_user_id, s.state_label
                   FROM user_identities i
                   LEFT JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id
                   WHERE i.vless_uuid IS NOT NULL"""
            )
            by_short = {r["internal_user_id"][:16]: r for r in recs}
            for sh in union:
                r = by_short.get(sh)
                meta[sh] = {
                    "tg": r["telegram_user_id"] if r else "?",
                    "state": r["state_label"] if r else "(no snapshot)",
                }

        # per-server missing vs union
        print("\n" + "=" * 80)
        print("Users in union but MISSING from each server (-> won't see it in /sub/)")
        print("=" * 80)
        for s in servers:
            missing = union - client_sets[s["id"]]
            tag = f"id={s['id']} {s['cc']} {s['label']}"
            if not missing:
                print(f"  {tag:<40} MISSING: 0  (full coverage)")
                continue
            print(f"  {tag:<40} MISSING: {len(missing)}")
            for sh in sorted(missing):
                m = meta.get(sh, {})
                print(f"      - {sh[:16]}  tg={m.get('tg')}  state={m.get('state')}")

        # focused summary for Russia
        russia = next((s for s in servers if s["id"] == 11), None)
        if russia:
            miss_russia = union - client_sets.get(11, set())
            print("\n" + "=" * 80)
            print(f"RUSSIA (id=11) — {len(client_sets.get(11, set()))} clients; "
                  f"{len(miss_russia)} users with keys elsewhere but NOT on Russia")
            print("=" * 80)
            for sh in sorted(miss_russia):
                m = meta.get(sh, {})
                print(f"  - {sh[:16]}  tg={m.get('tg')}  state={m.get('state')}")
            if not miss_russia:
                print("  (everyone with a key is on Russia)")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
