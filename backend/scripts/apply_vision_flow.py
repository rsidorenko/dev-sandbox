#!/usr/bin/env python3
"""One-time: set flow=xtls-rprx-vision on every BOT-managed client of every
Reality inbound, in BOTH inbounds.settings JSON and the v3 `clients` table.

WHY: Reality-TCP with an empty flow gets fingerprinted and blocked by RU border DPI.
xtls-rprx-vision is the only valid flow for Reality-TCP and resists that probing.
The bot now issues vision links + provisions vision clients; this migrates the
EXISTING clients so xray expects vision too. (reconcile skips existing clients, so
a bot restart alone does NOT update them — hence this script.)

SAFETY: only bot-managed clients (email prefix user-/x-user-/cdn-user-) are touched.
Service / inter-server relay UUIDs (e.g. the RU-relay→Helsinki chain UUID, whose
outbound sends flow="") are LEFT UNCHANGED so the relay cascade keeps matching.

Server-side, direct sqlite (no panel API): atomic, no login/TLS-verify concerns,
sets JSON + v3 table together. Idempotent. Caller restarts x-ui afterwards.

Run on the VPN server:  sudo python3 apply_vision_flow.py
"""
import json
import os
import sqlite3
import sys
import time

VISION = "xtls-rprx-vision"
# TARGET_FLOW: "xtls-rprx-vision" to apply vision, "" to REVERT to no-flow.
# `python3 apply_vision_flow.py revert`  -> "" (restore working state)
# `python3 apply_vision_flow.py`         -> vision (default)
if len(sys.argv) > 1 and sys.argv[1] == "revert":
    TARGET_FLOW = ""
else:
    TARGET_FLOW = os.environ.get("TARGET_FLOW", VISION)
BOT_PREFIXES = ("user-", "x-user-", "cdn-user-")
DB_CANDIDATES = (
    "/etc/x-ui/x-ui.db",
    "/usr/local/x-ui/x-ui.db",
    "/opt/x-ui/x-ui.db",
    "/root/x-ui/x-ui.db",
)


def _is_bot(email: str) -> bool:
    return any(email.startswith(p) for p in BOT_PREFIXES)


def _find_db() -> str | None:
    for p in DB_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def main():
    db_path = _find_db()
    if not db_path:
        print("!! no x-ui.db found — is 3x-ui installed here?")
        return
    print(f"DB: {db_path}")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    c = db.cursor()
    now_ms = int(time.time() * 1000)

    reality_inbound_ids: list[int] = []
    json_changed = 0
    json_skipped_service = 0

    for ib in c.execute("SELECT id, port, settings, stream_settings FROM inbounds").fetchall():
        try:
            ss = json.loads(ib["stream_settings"]) if ib["stream_settings"] else {}
        except json.JSONDecodeError:
            continue
        if ss.get("security") != "reality":
            continue  # only Reality inbounds (ws/tls inbounds untouched)
        reality_inbound_ids.append(ib["id"])
        try:
            settings = json.loads(ib["settings"]) if ib["settings"] else {}
        except json.JSONDecodeError:
            continue
        clients = settings.setdefault("clients", [])
        touched = False
        n_vision = 0
        for cl in clients:
            email = cl.get("email", "")
            if _is_bot(email):
                if cl.get("flow") != TARGET_FLOW:
                    cl["flow"] = TARGET_FLOW
                    touched = True
                    json_changed += 1
                n_vision += 1
            else:
                json_skipped_service += 1  # relay/service UUID — leave its flow alone
        if touched:
            c.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), ib["id"]))
        print(f"  inbound {ib['id']} (:{ib['port']} reality): bot clients={n_vision} "
              f"{'(updated)' if touched else '(already vision)'}")

    # v3 clients table — set vision only for bot clients linked to a reality inbound
    has_clients = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='clients'"
    ).fetchone()
    table_changed = 0
    if has_clients and reality_inbound_ids:
        placeholders = ",".join("?" for _ in reality_inbound_ids)
        # bot clients linked to any reality inbound
        rows = c.execute(
            f"""SELECT DISTINCT cl.id FROM clients cl
                JOIN client_inbounds ci ON ci.client_id = cl.id
                WHERE ci.inbound_id IN ({placeholders})
                  AND (cl.email LIKE 'user-%' OR cl.email LIKE 'x-user-%' OR cl.email LIKE 'cdn-user-%')
                  AND cl.flow != ?""",
            (*reality_inbound_ids, TARGET_FLOW),
        ).fetchall()
        for r in rows:
            c.execute("UPDATE clients SET flow=?, updated_at=? WHERE id=?", (TARGET_FLOW, now_ms, r["id"]))
            table_changed += 1
        total = c.execute("SELECT count(*) FROM clients").fetchone()[0]
        print(f"  v3 clients table: set vision on {table_changed} bot client rows (total clients={total})")
    elif not has_clients:
        print("  v3 clients table: absent (v2.x — JSON-only, nothing more to do)")

    db.commit()
    db.close()
    flow_desc = repr(TARGET_FLOW) if TARGET_FLOW else "(empty — no-flow)"
    print(f"\nSUMMARY: target flow={flow_desc} | JSON clients updated={json_changed} "
          f"(skipped {json_skipped_service} service/relay clients), table rows updated={table_changed}")
    print("Restart x-ui (sudo systemctl restart x-ui) for xray to reload.")


if __name__ == "__main__":
    main()
