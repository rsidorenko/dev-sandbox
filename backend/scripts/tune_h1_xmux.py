"""Tune the h1cloud h1-fork inbound xmux to stop unbounded connection accumulation.

Symptom: the «🇳🇱 Обход белых списков» key (id=22, node67.safetunn.shop CDN bypass) stops working
after a while; manual xray restart fixes it. Root cause: inbound streamSettings.xhttpSettings.
extra.xmux has cMaxLifetimeMs=0 (connections never expire by age) and maxConnections=0 — so xmux
connections accumulate without bound, exhausting the xray process until new handshakes fail.
Restart clears the pool (hence the manual fix working).

Fix: set cMaxLifetimeMs to a finite value (900000 ms = 15 min, aligned with hMaxReusableSecs
720-1800) so connections recycle by age and the pool reaches a bounded steady state. We do NOT
cap maxConnections — on the server inbound that risks throttling legitimate concurrency for 33+
active users. cMaxLifetimeMs is a pure recycling fix (no rejection risk).

Idempotent: reads inbound, sets the value only if different, restarts xray only if changed.
The inbound streamSettings persists across restarts (unlike panel-level xrayTemplateConfig), so
this is a one-time tune. Run in the production container with DATABASE_URL.
"""
import asyncio
import json
import os
import sys

NEW_CMAX_LIFETIME_MS = 900000  # 15 minutes

TARGET_PATH = ("xhttpSettings", "extra", "xmux", "cMaxLifetimeMs")


async def run() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg
    from app.security.field_encryption import decrypt_field

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    try:
        r = await pool.fetchrow(
            """SELECT id, label, panel_url, panel_username, panel_password,
                      COALESCE(encrypted_password,'') AS enc, inbound_id
               FROM vpn_servers WHERE panel_flavor='h1' AND is_active=TRUE ORDER BY id LIMIT 1""")
        if not r:
            print("ERROR: no active h1 row (panel_flavor='h1') found", file=sys.stderr)
            sys.exit(1)
        pw = decrypt_field(r["enc"]) if r["enc"] else r["panel_password"]
        base = r["panel_url"].rstrip("/")
        inbound_id = r["inbound_id"]
        print(f"row id={r['id']} label={r['label']!r} inbound_id={inbound_id} panel={base}")
    finally:
        await pool.close()

    import httpx
    async with httpx.AsyncClient(verify=False, timeout=20, follow_redirects=False) as c:
        await c.get(f"{base}/")
        csrf_r = await c.get(f"{base}/csrf-token")
        csrf = csrf_r.json().get("obj", "") if csrf_r.status_code == 200 else ""
        lr = await c.post(f"{base}/login", json={"username": r["panel_username"], "password": pw},
                          headers={"X-CSRF-Token": csrf})
        if lr.status_code != 200 or not lr.json().get("success"):
            print(f"login failed: {lr.status_code} {lr.text[:200]}", file=sys.stderr)
            sys.exit(1)
        h = {"X-CSRF-Token": csrf}

        gr = await c.get(f"{base}/panel/api/inbounds/get/{inbound_id}", headers=h)
        ib = gr.json()["obj"]
        ss = ib.get("streamSettings")
        ss_was_str = isinstance(ss, str)
        if ss_was_str:
            ss = json.loads(ss)
        ss_obj = ss if isinstance(ss, dict) else {}

        # navigate to the xmux dict
        cur = ss_obj
        for k in TARGET_PATH[:-1]:
            if not isinstance(cur.get(k), dict):
                print(f"ERROR: streamSettings has no {'.'.join(TARGET_PATH[:-1])} (got {type(cur.get(k))})", file=sys.stderr)
                sys.exit(1)
            cur = cur[k]
        leaf = TARGET_PATH[-1]
        old = cur.get(leaf)
        print(f"current {leaf} = {old}")
        if old == NEW_CMAX_LIFETIME_MS:
            print("already tuned — no change, no restart")
            return
        cur[leaf] = NEW_CMAX_LIFETIME_MS
        new_ss = json.dumps(ss_obj) if ss_was_str else ss_obj
        print(f"new {leaf} = {NEW_CMAX_LIFETIME_MS}")

        payload = {
            "id": ib["id"],
            "settings": ib.get("settings") if isinstance(ib.get("settings"), str) else json.dumps(ib.get("settings", {})),
            "streamSettings": new_ss,
            "sniffing": ib.get("sniffing") if isinstance(ib.get("sniffing"), str) else json.dumps(ib.get("sniffing", {})),
            "protocol": ib["protocol"], "port": ib["port"], "listen": ib.get("listen", ""),
            "tag": ib.get("tag", ""), "remark": ib.get("remark", ""), "enable": ib.get("enable", True),
            "expiryTime": ib.get("expiryTime", 0), "total": ib.get("total", 0),
            "up": ib.get("up", 0), "down": ib.get("down", 0),
        }
        ur = await c.post(f"{base}/panel/api/inbounds/update/{inbound_id}", json=payload, headers=h)
        ub = ur.json() if ur.status_code == 200 else {}
        print(f"update inbound: {ur.status_code} success={ub.get('success')}")
        if not ub.get("success"):
            print(ub, file=sys.stderr); sys.exit(1)

        # restart xray (h1-fork does NOT hot-apply streamSettings changes)
        rr = await c.post(f"{base}/panel/api/server/restartXrayService", json={}, headers=h)
        rb = rr.json() if rr.status_code == 200 else {}
        print(f"restartXray: {rr.status_code} success={rb.get('success')}")

        # verify
        gr2 = await c.get(f"{base}/panel/api/inbounds/get/{inbound_id}", headers=h)
        ss2 = gr2.json()["obj"].get("streamSettings")
        if isinstance(ss2, str):
            ss2 = json.loads(ss2)
        v = ss2
        for k in TARGET_PATH:
            v = v[k]
        print(f"VERIFY: persisted {leaf} = {v}  ({'OK' if v == NEW_CMAX_LIFETIME_MS else 'MISMATCH'})")


if __name__ == "__main__":
    asyncio.run(run())
