"""RU egress relay: route Russian domains/IPs on foreign VPN panels to the RU
relay server (89.169.139.153), and repurpose that server as a relay-only target.

This implements the "flip the relay direction" change:

  BEFORE: the 🇷🇺 server (id=11, 89.169.139.153) was a USER-FACING VPN server with
          internal split routing (.ru/.su/.рф + geoip:ru -> direct, everything else
          -> Helsinki). Users had to PICK it, and foreign traffic did an extra hop.

  AFTER:  89.169.139.153 becomes a relay-only TARGET. Foreign panels (Helsinki /
          Frankfurt / LA) get a NON-DESTRUCTIVE merge into their xrayTemplateConfig:
          one outbound `relay-to-russia` (VLESS+Reality -> 89.169.139.153:443) plus
          two routing rules (.ru/.su/.рф + geoip:ru -> relay). The catch-all stays
          `direct`, so non-Russian traffic behaviour is byte-identical to before;
          only Russian destinations now egress from the RU IP. id=11 is deactivated
          so users stop connecting to 🇷🇺 directly (the 🇷🇺 entry leaves /sub/).

The RU relay's OWN routing is NOT touched: it already does `RU -> direct`, which is
exactly the egress a relay target needs. We only register one shared relay UUID on
its inbound so the foreign panels can connect as that client.

Modes (--mode):
  dump          READ-ONLY. Run ON a panel: print its current xrayTemplateConfig
                (routing rules + full outbound JSON), geo files, :443, xray.
  register-uuid Run ON the RU relay (89.169.139.153): register the shared relay
                UUID on its :443 inbound (clients + client_inbounds + settings
                JSON — the 3-place pattern for 3x-ui v3). Idempotent.
                Env RU_RELAY_INBOUND (default 3).
  export        READ-ONLY. Run ON the SOURCE panel (e.g. Frankfurt): emit the
                working ru-relay outbound + its routing rules as JSON
                ({"outbound": {...}, "rules": [...]}). Env RU_RELAY_EXPORT_TAG
                (default "ru-relay").
  import        Run ON the TARGET panel (e.g. Helsinki): merge an exported
                ru-relay config (env RU_RELAY_EXPORT = base64 JSON from `export`)
                into xrayTemplateConfig so the target matches the source exactly
                (same tag, same UUID, same rules). Backs up first; ensures geo
                files; idempotent; restarts x-ui + verifies.
  apply         Run ON a foreign panel: merge a built-in relay-to-russia outbound
                + RU rules (.ru/.su/.рф + geoip:ru) into xrayTemplateConfig. An
                alternative to export/import (different tag/rules style). Backs up,
                idempotent, restarts x-ui + verifies.
  repoint       Run ON a foreign panel: update the EXISTING ru-relay outbound's
                target to the canonical RU relay (89.169.139.153, pbk/sid/sni=max.ru,
                UUID 00607f0b) — keeping its tag + all routing rules intact. Used to
                correct a panel whose ru-relay points at the wrong/old relay IP.
                Env RU_RELAY_EXPORT_TAG (default "ru-relay"). Backs up, idempotent,
                restarts x-ui + verifies. Requires the UUID registered on the RU
                relay inbound first (see register-uuid).
  revert        Run ON a foreign panel: restore the pre-apply/pre-import/pre-repoint
                xrayTemplateConfig from the backup.
  purge-orphans Run ON the RU relay: remove the orphaned per-user clients left on its
                inbound from when it was a user-facing 🇷🇺 server. Keeps the relay
                UUID (and anything non-user-format). Env RU_RELAY_INBOUND (default 3).
                Idempotent. Restarts x-ui + verifies.
  fix-sniffing  Run ON a foreign panel: standardize each user-facing inbound's
                sniffing to destOverride [http,tls,quic] + routeOnly false (the
                config that makes domain-based RU routing work — proven on the
                tcp/xhttp inbounds). Fixes the ws/cdn (2.0) inbound whose
                routeOnly:true broke RU-relay routing. Idempotent; restarts + verifies.
  deactivate    Run IN the prod container (DATABASE_URL): set vpn_servers id=11
                is_active=FALSE. Reversible (set TRUE again).

Safety invariants:
  - apply is a MERGE, never a clobber: existing outbounds/rules are preserved.
  - The pure merge logic lives in merge_ru_routing() so it is unit-testable.
  - register-uuid and apply are idempotent (safe to re-run).
  - restart_and_verify fails loudly if xray does not come back up.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

# ── RU relay target params (the foreign panels connect TO this) ──────────────
# Sourced from the RU relay's live inbound stream_settings (relay_probe run
# 27506043973): publicKey ouYwM6.., serverNames [max.ru], shortId a1b2c3d4e5f6.
RU_RELAY_HOST = os.environ.get("RU_RELAY_HOST", "89.169.139.153")
RU_RELAY_PORT = int(os.environ.get("RU_RELAY_PORT", "443"))
RU_RELAY_PBK = os.environ.get("RU_RELAY_PBK", "ouYwM6eddxNLHx5kJ51hfdQxdNcBRwDxLfJWdTERT14")
RU_RELAY_SID = os.environ.get("RU_RELAY_SID", "a1b2c3d4e5f6")
RU_RELAY_SNI = os.environ.get("RU_RELAY_SNI", "max.ru")

# Shared relay UUID — reused from the old RU->Helsinki direction (was registered
# on Helsinki; now we move it to the RU relay's inbound). Constant keeps the
# foreign outbounds + the RU relay client in sync.
RELAY_UUID = os.environ.get("RU_RELAY_UUID", "00607f0b-a9e7-4280-abb3-2231e1b9c2ff")
RELAY_EMAIL = "relay-from-foreign"

# Bot-provisioned per-user client emails follow this shape (transport prefixes:
# tcp="", cdn="cdn-", xhttp="x-", then "user-<id>"). The relay UUID uses
# "relay-from-foreign" — does NOT match, so purge-orphans keeps it.
_USER_CLIENT_EMAIL_RE = re.compile(r"^(?:x-|cdn-)?user-")

# Standard inbound sniffing that makes domain-based routing work (matches the
# tcp/xhttp inbounds where RU-relay routing is proven). routeOnly:false so the
# sniffed domain drives BOTH routing and the connection; quic so HTTP/3 domains
# are sniffed too. The ws/cdn (2.0) inbound had routeOnly:true + no quic, which
# broke RU-relay routing for 2.0 users.
STANDARD_SNIFFING = {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": False}
STANDARD_SNIFFING_JSON = json.dumps(STANDARD_SNIFFING, separators=(", ", ": "))


def _is_user_client(email: str) -> bool:
    """True if *email* is a bot-provisioned per-user client (vs the relay UUID
    or anything else). Pure + unit-tested."""
    return bool(email and _USER_CLIENT_EMAIL_RE.match(email))


def standardize_sniffing(sniffing_str: str) -> str | None:
    """Pure: return the standard sniffing JSON if *sniffing_str* differs from it,
    else None (already standard). Tolerates malformed/empty input."""
    try:
        cur = json.loads(sniffing_str) if sniffing_str else {}
    except Exception:
        cur = {}
    return None if cur == STANDARD_SNIFFING else STANDARD_SNIFFING_JSON

RELAY_OUTBOUND_TAG = "relay-to-russia"
# xn--p1ai is the punycode for .рф. TLD rules need no geo file.
RU_DOMAINS = ["domain:ru", "domain:su", "domain:xn--p1ai"]

GEOIP_URL = "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat"
GEOSITE_URL = "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat"

DB_CANDIDATES = [
    "/etc/x-ui/x-ui.db",
    "/usr/local/x-ui/x-ui.db",
    "/usr/local/x-ui/bin/x-ui.db",
    "/opt/x-ui/x-ui.db",
]
BACKUP_SUFFIX = ".xrayTemplateConfig.pre-ru-egress.bak"


# ── helpers ──────────────────────────────────────────────────────────────────

def run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"CMD FAILED: {cmd}\nstderr: {r.stderr}", file=sys.stderr)
    return r


def find_db() -> str | None:
    for p in DB_CANDIDATES:
        if os.path.exists(p):
            return p
    r = run(
        "find /etc /usr/local /opt /root /var/lib -name 'x-ui.db' -type f 2>/dev/null | head -1",
        check=False,
    )
    line = r.stdout.strip().splitlines()
    return line[0] if line else None


def geo_dir() -> str:
    for d in ("/usr/local/x-ui/bin", "/etc/x-ui", "/opt/x-ui/bin"):
        if os.path.isdir(d):
            return d
    return "/usr/local/x-ui/bin"


def ensure_geo_files() -> None:
    """geoip.dat (for geoip:ru) and geosite.dat (for geosite:category-ru / tld-ru)
    must be present. Downloads from Loyalsoldier if missing/suspiciously small."""
    d = geo_dir()
    for name, url in (("geoip.dat", GEOIP_URL), ("geosite.dat", GEOSITE_URL)):
        path = os.path.join(d, name)
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            print(f"{name} present ({os.path.getsize(path)} bytes)")
            continue
        print(f"{name} missing/small -> downloading to {path}")
        r = run(f"curl -Ls -o {path} {url} && test -s {path}", check=False)
        if r.returncode != 0:
            run(f"wget -q -O {path} {url}", check=False)
        ok = os.path.exists(path) and os.path.getsize(path) > 1_000_000
        print(f"{name} after download: {'OK' if ok else 'FAILED'}")
        if not ok:
            print(f"ERROR: {name} unavailable — RU routing rules need it", file=sys.stderr)
            sys.exit(1)


def restart_and_verify() -> None:
    """Restart x-ui and fail loudly if xray does not come back up / :443 unbound.
    xray crashes silently on bad geo/routing; always verify post-restart."""
    run("sudo systemctl restart x-ui", check=False)
    time.sleep(8)
    xray = run("pgrep -f xray-linux-amd64", check=False)
    port = run("sudo ss -tlnp | grep -E ':443 '", check=False)
    xray_ok = bool(xray.stdout.strip())
    port_ok = bool(port.stdout.strip())
    print(f"xray running: {xray_ok}")
    print(f":443 listening: {port_ok}")
    if not xray_ok or not port_ok:
        print("ERROR: xray failed to start or :443 not bound after restart.", file=sys.stderr)
        j = run(
            "sudo journalctl -u x-ui --no-pager -n 20 2>/dev/null | grep -iE 'error|failed|geodata'",
            check=False,
        )
        print(j.stdout, file=sys.stderr)
        sys.exit(1)


# ── pure merge logic (unit-tested) ───────────────────────────────────────────

def _relay_outbound() -> dict:
    return {
        "tag": RELAY_OUTBOUND_TAG,
        "protocol": "vless",
        "settings": {"vnext": [{"address": RU_RELAY_HOST, "port": RU_RELAY_PORT,
                                "users": [{"id": RELAY_UUID, "encryption": "none", "flow": ""}]}]},
        "streamSettings": {
            "network": "tcp", "security": "reality",
            "realitySettings": {"serverName": RU_RELAY_SNI, "fingerprint": "chrome",
                                "publicKey": RU_RELAY_PBK, "shortId": RU_RELAY_SID},
            "tcpSettings": {"header": {"type": "none"}},
        },
    }


def _ru_rules() -> list[dict]:
    return copy.deepcopy([
        {"type": "field", "domain": RU_DOMAINS, "outboundTag": RELAY_OUTBOUND_TAG},
        {"type": "field", "ip": ["geoip:ru"], "outboundTag": RELAY_OUTBOUND_TAG},
    ])


def merge_ru_routing(template: dict) -> dict:
    """NON-DESTRUCTIVE merge of the RU egress relay into a copy of an
    xrayTemplateConfig dict.

    - sets routing.domainStrategy = IPIfNonMatch (needed so geoip:ru resolves
      domain-based connections to IPs before matching);
    - adds the relay-to-russia outbound (replacing any prior one — idempotent);
    - inserts the two RU routing rules right after the api rule (and after a
      geoip:private rule if present), so api/private traffic is handled first.

    Existing outbounds and rules are preserved. The catch-all (default route to
    the first outbound, normally `direct`) is untouched -> non-RU traffic stays
    exactly as before. Idempotent: merging an already-merged template is a no-op.
    """
    t = copy.deepcopy(template)
    routing = t.setdefault("routing", {})
    routing["domainStrategy"] = "IPIfNonMatch"
    rules = routing.setdefault("rules", [])
    outbounds = t.setdefault("outbounds", [])

    # (Re)place the relay outbound — drop any stale copy first (idempotent).
    outbounds[:] = [o for o in outbounds if o.get("tag") != RELAY_OUTBOUND_TAG]
    outbounds.append(_relay_outbound())

    # Drop any stale RU rules (idempotent), then re-insert at the right spot.
    rules[:] = [r for r in rules if r.get("outboundTag") != RELAY_OUTBOUND_TAG]
    insert_at = 0
    for i, r in enumerate(rules):
        if r.get("outboundTag") == "api":
            insert_at = i + 1
        ips = r.get("ip") or []
        if isinstance(ips, list) and any("geoip:private" in str(x) for x in ips):
            insert_at = i + 1
    rules[insert_at:insert_at] = _ru_rules()
    return t


def apply_exported_routing(template: dict, outbound: dict, rules: list) -> dict:
    """NON-DESTRUCTIVE merge of an EXPORTED ru-relay config (from `export`) into a
    copy of a target panel's xrayTemplateConfig.

    This is the data-driven path to make one panel match another's working ru-relay
    config byte-for-byte (same tag, same UUID, same Reality params, same rules).
    - sets routing.domainStrategy = IPIfNonMatch (so geoip:ru resolves);
    - (re)places the exported outbound (by its tag — idempotent);
    - removes any prior rules with that outboundTag (idempotent), then inserts the
      exported rules right after the api rule / a geoip:private rule.

    Existing outbounds/rules are preserved. Idempotent. Unit-tested.
    """
    t = copy.deepcopy(template)
    tag = outbound.get("tag", "ru-relay")
    routing = t.setdefault("routing", {})
    routing["domainStrategy"] = "IPIfNonMatch"
    trules = routing.setdefault("rules", [])
    toutbounds = t.setdefault("outbounds", [])

    toutbounds[:] = [o for o in toutbounds if o.get("tag") != tag]
    toutbounds.append(copy.deepcopy(outbound))

    trules[:] = [r for r in trules if r.get("outboundTag") != tag]
    insert_at = 0
    for i, r in enumerate(trules):
        if r.get("outboundTag") == "api":
            insert_at = i + 1
        ips = r.get("ip") or []
        if isinstance(ips, list) and any("geoip:private" in str(x) for x in ips):
            insert_at = i + 1
    trules[insert_at:insert_at] = copy.deepcopy(rules)
    return t


def repoint_ru_relay_outbound(template: dict, tag: str = "ru-relay") -> dict:
    """Update an EXISTING ru-relay outbound's target to the canonical RU relay
    (89.169.139.153) — its address/port, the registered relay UUID, and the Reality
    publicKey/shortId/serverName — while keeping the outbound's tag and ALL routing
    rules untouched. Raises ValueError if no outbound with *tag* exists.

    Used to correct a panel whose ru-relay points at a wrong/old relay IP (e.g.
    51.250.102.219) so all panels relay to 89.169.139.153. Pure + unit-tested.
    """
    t = copy.deepcopy(template)
    outbounds = t.get("outbounds", [])
    ob = next((o for o in outbounds if o.get("tag") == tag), None)
    if ob is None:
        raise ValueError(f"no outbound with tag {tag!r} to repoint")
    settings = ob.setdefault("settings", {})
    vnext = settings.setdefault("vnext", [{}])
    if not vnext:
        vnext.append({})
    vnext[0]["address"] = RU_RELAY_HOST          # 89.169.139.153
    vnext[0]["port"] = RU_RELAY_PORT             # 443
    users = vnext[0].setdefault("users", [{}])
    if not users:
        users.append({})
    users[0]["id"] = RELAY_UUID                  # 00607f0b-… (registered on RU relay)
    users[0]["encryption"] = "none"
    users[0]["flow"] = ""
    ss = ob.setdefault("streamSettings", {})
    ss["network"] = "tcp"
    ss["security"] = "reality"
    rs = ss.setdefault("realitySettings", {})
    rs["publicKey"] = RU_RELAY_PBK              # ouYwM6…
    rs["shortId"] = RU_RELAY_SID                # a1b2c3d4e5f6
    rs["serverName"] = RU_RELAY_SNI             # max.ru
    rs["fingerprint"] = "chrome"
    return t


# ── modes ────────────────────────────────────────────────────────────────────

def cmd_dump() -> None:
    db = find_db()
    print(f"DB: {db}")
    if not db:
        print("ERROR: no x-ui.db found", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    row = cur.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
    if row:
        t = json.loads(row[0])
        routing = t.get("routing", {}) or {}
        print(f"domainStrategy: {routing.get('domainStrategy')}")
        print("outbounds:")
        for o in t.get("outbounds", []):
            print("  ", json.dumps(o, ensure_ascii=False))
        print("routing rules:")
        for r in routing.get("rules", []):
            print("  ", json.dumps(r, ensure_ascii=False))
    else:
        print("no xrayTemplateConfig in settings")
    # inbound list (quick sanity)
    try:
        for ib in cur.execute("SELECT id, port, protocol, tag, sniffing FROM inbounds").fetchall():
            print(f"  inbound: id={ib[0]} port={ib[1]} proto={ib[2]} tag={ib[3]}")
            print(f"    sniffing: {ib[4]}")
    except Exception as e:
        print(f"  (inbound list skipped: {e})")
    conn.close()
    print("geo files:")
    print(run("ls -la /usr/local/x-ui/bin/*.dat 2>/dev/null").stdout or "  (none)")
    print(":443:", run("ss -tlnp 2>/dev/null | grep ':443 '").stdout.strip() or "(not listening)")
    print(":8080 (ws/cdn 2.0):", run("ss -tlnp 2>/dev/null | grep ':8080 '").stdout.strip() or "(not listening)")
    print(":8443 (xhttp 3.0):", run("ss -tlnp 2>/dev/null | grep ':8443 '").stdout.strip() or "(not listening)")
    print("xray:", run("pgrep -af '[x]ray'").stdout.strip() or "(not running)")
    # Local xray access log: shows how each inbound's traffic is actually routed
    # ([inbound-tag >> outbound-tag]). Filter to REAL traffic (exclude the 127.0.0.1
    # api pings that flood the log every 5s) so a 2.0 (ws) user's RU request and its
    # routing decision ([inbound-8080 >> ru-relay|direct|blocked]) is visible.
    print("xray-access log — REAL traffic (last 1000 lines, excluding 127.0.0.1 api):")
    for p in ("/var/log/xray-access.log", "/usr/local/x-ui/access.log"):
        r = run(f"sudo tail -1000 {p} 2>/dev/null | grep -v '127.0.0.1' | tail -60")
        if r.stdout.strip():
            print(f"--- {p} (real-traffic lines) ---")
            print(r.stdout.strip())
            cnt = run(f"sudo tail -2000 {p} 2>/dev/null | grep -v '127.0.0.1' | wc -l")
            print(f"(real-traffic lines in last 2000: {cnt.stdout.strip()})")
            break
    else:
        print("  (no access log found)")
    print("xray-error log (last 20):")
    for p in ("/var/log/xray-error.log", "/usr/local/x-ui/error.log"):
        r = run(f"sudo tail -20 {p} 2>/dev/null")
        if r.stdout.strip():
            print(f"--- {p} ---")
            print(r.stdout.strip())
            break
    else:
        print("  (no error log found)")


def cmd_register_uuid() -> None:
    db = find_db()
    if not db:
        print("ERROR: no x-ui.db found", file=sys.stderr)
        sys.exit(1)
    inbound_id = int(os.environ.get("RU_RELAY_INBOUND", "3"))
    conn = sqlite3.connect(db)
    cur = conn.cursor()

    # 1. clients table
    row = cur.execute("SELECT id FROM clients WHERE uuid=?", (RELAY_UUID,)).fetchone()
    if row:
        client_id = row[0]
        print(f"relay UUID already in clients table (id={client_id})")
    else:
        now = int(time.time())
        cur.execute(
            "INSERT INTO clients (email,uuid,enable,flow,limit_ip,total_gb,expiry_time,reset,"
            "created_at,updated_at) VALUES (?,?,?,?,0,0,0,0,?,?)",
            (RELAY_EMAIL, RELAY_UUID, 1, "", now, now),
        )
        client_id = cur.lastrowid
        print(f"added relay UUID to clients table (id={client_id})")

    # 2. client_inbounds link
    link = cur.execute(
        "SELECT 1 FROM client_inbounds WHERE client_id=? AND inbound_id=?", (client_id, inbound_id)
    ).fetchone()
    if not link:
        cur.execute(
            "INSERT INTO client_inbounds (client_id,inbound_id) VALUES (?,?)", (client_id, inbound_id)
        )
        print(f"linked client -> inbound {inbound_id}")
    else:
        print(f"client_inbounds link already exists (inbound {inbound_id})")

    # 3. inbounds.settings JSON clients array
    srow = cur.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()
    if not srow:
        print(f"ERROR: inbound {inbound_id} not found", file=sys.stderr)
        sys.exit(1)
    settings = json.loads(srow[0]) if srow[0] else {}
    clients = settings.setdefault("clients", [])
    if not any(c.get("id") == RELAY_UUID for c in clients):
        clients.append({"id": RELAY_UUID, "email": RELAY_EMAIL, "enable": True, "flow": ""})
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), inbound_id))
        print("added relay UUID to inbound settings.clients JSON")
    else:
        print("relay UUID already in inbound settings JSON")

    conn.commit()
    conn.close()
    print("register-uuid complete -> restarting x-ui")
    restart_and_verify()


def cmd_purge_orphans() -> None:
    """On the RU relay: remove the orphaned per-user clients left on its inbound
    from when it was a user-facing 🇷🇺 server (now id=11 is inactive). Keeps the
    relay UUID (relay-from-foreign) and anything non-user-format. Idempotent."""
    db = find_db()
    if not db:
        print("ERROR: no x-ui.db found", file=sys.stderr)
        sys.exit(1)
    inbound_id = int(os.environ.get("RU_RELAY_INBOUND", "3"))
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT c.id, c.email, c.uuid FROM clients c "
        "JOIN client_inbounds ci ON ci.client_id = c.id WHERE ci.inbound_id = ?",
        (inbound_id,),
    ).fetchall()
    to_remove = [r for r in rows if _is_user_client(r[1] or "")]
    keep = [r for r in rows if not _is_user_client(r[1] or "")]
    print(f"inbound {inbound_id}: {len(rows)} clients; "
          f"remove {len(to_remove)} user-format orphans; "
          f"keep {len(keep)} -> {[f'{r[1]}({r[2][:8]})' for r in keep]}")
    if not to_remove:
        print("nothing to purge")
        conn.close()
        return
    for cid, _email, _uuid in to_remove:
        cur.execute("DELETE FROM client_inbounds WHERE client_id = ?", (cid,))
        cur.execute("DELETE FROM clients WHERE id = ?", (cid,))
    srow = cur.execute("SELECT settings FROM inbounds WHERE id = ?", (inbound_id,)).fetchone()
    if srow and srow[0]:
        settings = json.loads(srow[0])
        clients = settings.get("clients", [])
        before = len(clients)
        settings["clients"] = [c for c in clients if not _is_user_client(c.get("email", "") or "")]
        after = len(settings["clients"])
        cur.execute("UPDATE inbounds SET settings = ? WHERE id = ?",
                    (json.dumps(settings), inbound_id))
        print(f"settings.clients JSON: {before} -> {after}")
    conn.commit()
    conn.close()
    print(f"purged {len(to_remove)} orphaned user clients (relay UUID preserved)")
    print("purge-orphans complete -> restarting x-ui")
    restart_and_verify()


def cmd_fix_sniffing() -> None:
    """Standardize each user-facing inbound's sniffing to destOverride
    [http,tls,quic] + routeOnly false — the config that makes domain-based RU
    routing work. Fixes the ws/cdn (2.0) inbound whose routeOnly:true broke
    RU-relay routing. Skips the api (dokodemo-door) inbound."""
    db = find_db()
    if not db:
        print("ERROR: no x-ui.db found", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    rows = cur.execute("SELECT id, tag, sniffing FROM inbounds").fetchall()
    changes = []
    for inbound_id, tag, sniff_str in rows:
        if (tag or "") == "api":
            continue
        new = standardize_sniffing(sniff_str or "")
        if new is not None:
            changes.append((inbound_id, tag, sniff_str, new))
    if not changes:
        print("all user inbounds already have standard sniffing — nothing to fix")
        conn.close()
        return
    backup_path = db + BACKUP_SUFFIX
    # (reuse the same backup slot; revert restores the panel template, not inbound
    # rows — so we print what we change for an audit trail instead.)
    for inbound_id, tag, old, new in changes:
        print(f"  inbound id={inbound_id} tag={tag}: {old}  ->  {new}")
        cur.execute("UPDATE inbounds SET sniffing=? WHERE id=?", (new, inbound_id))
    conn.commit()
    conn.close()
    print(f"fix-sniffing: updated {len(changes)} inbound(s)")
    print("fix-sniffing complete -> restarting x-ui")
    restart_and_verify()


def cmd_apply() -> None:
    db = find_db()
    if not db:
        print("ERROR: no x-ui.db found", file=sys.stderr)
        sys.exit(1)
    ensure_geo_files()

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    row = cur.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
    if not row or not row[0]:
        print("ERROR: no xrayTemplateConfig in settings", file=sys.stderr)
        sys.exit(1)
    original_str = row[0]
    template = json.loads(original_str)

    # Backup the pre-apply template ONCE (re-running apply keeps the first backup).
    backup_path = db + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        with open(backup_path, "w") as f:
            f.write(original_str)
        print(f"backup written: {backup_path}")
    else:
        print(f"backup already exists (preserved): {backup_path}")

    merged = merge_ru_routing(template)
    cur.execute(
        "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'", (json.dumps(merged),)
    )
    conn.commit()
    conn.close()
    print("merged relay-to-russia outbound + RU rules; domainStrategy=IPIfNonMatch")
    print("apply complete -> restarting x-ui")
    restart_and_verify()


def cmd_export() -> None:
    """READ-ONLY. Emit the working ru-relay outbound + its routing rules as JSON.
    Run on the SOURCE panel (Frankfurt). Output is consumed by `import`."""
    db = find_db()
    if not db:
        print("ERROR: no x-ui.db found", file=sys.stderr)
        sys.exit(1)
    tag = os.environ.get("RU_RELAY_EXPORT_TAG", "ru-relay")
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    row = cur.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
    conn.close()
    if not row or not row[0]:
        print("ERROR: no xrayTemplateConfig in settings", file=sys.stderr)
        sys.exit(1)
    template = json.loads(row[0])
    outbound = next((o for o in template.get("outbounds", []) if o.get("tag") == tag), None)
    if not outbound:
        print(f"ERROR: no outbound with tag {tag!r} on this panel", file=sys.stderr)
        sys.exit(1)
    rules = [r for r in template.get("routing", {}).get("rules", [])
             if r.get("outboundTag") == tag]
    # Single JSON line on stdout (the workflow base64s it for `import`).
    print(json.dumps({"outbound": outbound, "rules": rules}, ensure_ascii=False))


def cmd_import() -> None:
    """Merge an exported ru-relay config (env RU_RELAY_EXPORT = base64 JSON) into
    this panel's xrayTemplateConfig so it matches the source. Run on the TARGET."""
    raw_b64 = os.environ.get("RU_RELAY_EXPORT", "").strip()
    if not raw_b64:
        print("ERROR: RU_RELAY_EXPORT env not set (base64 JSON from `export`)", file=sys.stderr)
        sys.exit(1)
    import base64

    try:
        export = json.loads(base64.b64decode(raw_b64).decode("utf-8"))
    except Exception as e:
        print(f"ERROR: could not decode RU_RELAY_EXPORT: {e}", file=sys.stderr)
        sys.exit(1)
    outbound = export.get("outbound")
    rules = export.get("rules", [])
    if not outbound or not outbound.get("tag"):
        print("ERROR: export missing 'outbound' with a 'tag'", file=sys.stderr)
        sys.exit(1)

    db = find_db()
    if not db:
        print("ERROR: no x-ui.db found", file=sys.stderr)
        sys.exit(1)
    ensure_geo_files()

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    row = cur.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
    if not row or not row[0]:
        print("ERROR: no xrayTemplateConfig in settings", file=sys.stderr)
        sys.exit(1)
    original_str = row[0]
    template = json.loads(original_str)

    backup_path = db + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        with open(backup_path, "w") as f:
            f.write(original_str)
        print(f"backup written: {backup_path}")
    else:
        print(f"backup already exists (preserved): {backup_path}")

    merged = apply_exported_routing(template, outbound, rules)
    cur.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
                (json.dumps(merged),))
    conn.commit()
    conn.close()
    print(f"imported ru-relay config (tag={outbound.get('tag')}, "
          f"{len(rules)} rules) -> matching source panel")
    print("import complete -> restarting x-ui")
    restart_and_verify()


def cmd_repoint() -> None:
    """Repoint the existing ru-relay outbound to the canonical RU relay
    (89.169.139.153), keeping its tag + all routing rules. Run on each foreign
    panel whose ru-relay targets a wrong/old relay IP."""
    db = find_db()
    if not db:
        print("ERROR: no x-ui.db found", file=sys.stderr)
        sys.exit(1)
    tag = os.environ.get("RU_RELAY_EXPORT_TAG", "ru-relay")

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    row = cur.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
    if not row or not row[0]:
        print("ERROR: no xrayTemplateConfig in settings", file=sys.stderr)
        sys.exit(1)
    original_str = row[0]
    template = json.loads(original_str)

    # Sanity: the ru-relay outbound must exist (else there's nothing to repoint).
    if not any(o.get("tag") == tag for o in template.get("outbounds", [])):
        print(f"ERROR: no outbound with tag {tag!r} on this panel — nothing to repoint "
              f"(use apply/import first)", file=sys.stderr)
        sys.exit(1)

    # Detect the current target to log what we're changing from.
    cur_ob = next(o for o in template["outbounds"] if o.get("tag") == tag)
    cur_addr = (cur_ob.get("settings", {}).get("vnext", [{}])[0].get("address"))
    print(f"current {tag} target: {cur_addr}")

    backup_path = db + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        with open(backup_path, "w") as f:
            f.write(original_str)
        print(f"backup written: {backup_path}")
    else:
        print(f"backup already exists (preserved): {backup_path}")

    merged = repoint_ru_relay_outbound(template, tag)
    cur.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
                (json.dumps(merged),))
    conn.commit()
    conn.close()
    print(f"repointed {tag} -> {RU_RELAY_HOST}:{RU_RELAY_PORT} (sni={RU_RELAY_SNI}, "
          f"uuid={RELAY_UUID[:8]}…); tag + routing rules preserved")
    print("repoint complete -> restarting x-ui")
    restart_and_verify()


def cmd_revert() -> None:
    db = find_db()
    if not db:
        print("ERROR: no x-ui.db found", file=sys.stderr)
        sys.exit(1)
    backup_path = db + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        print("ERROR: no pre-apply backup found — nothing to revert", file=sys.stderr)
        sys.exit(1)
    with open(backup_path) as f:
        original = f.read()
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute(
        "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'", (original,)
    )
    conn.commit()
    conn.close()
    print("restored pre-apply xrayTemplateConfig from backup")
    print("revert complete -> restarting x-ui")
    restart_and_verify()


async def cmd_deactivate() -> None:
    """Flip vpn_servers id=11 is_active -> FALSE (users stop seeing 🇷🇺; bot stops
    managing it). Runs in the prod container with DATABASE_URL."""
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)
    import asyncpg

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    try:
        before = await pool.fetchrow(
            "SELECT id, label, server_host, is_active FROM vpn_servers WHERE id = 11"
        )
        print(f"before: {dict(before) if before else '(no row id=11)'}")
        result = await pool.execute(
            "UPDATE vpn_servers SET is_active = FALSE WHERE id = 11"
        )
        print(f"UPDATE: {result}")
        after = await pool.fetchrow(
            "SELECT id, label, is_active FROM vpn_servers WHERE id = 11"
        )
        print(f"after: {dict(after) if after else '(no row id=11)'}")
        active = await pool.fetchval("SELECT count(*) FROM vpn_servers WHERE is_active = TRUE")
        print(f"total active servers now: {active}")
        print("NOTE: 🇷🇺 leaves every /sub/ on next fetch (config cache TTL 600s). "
              "Reversible: UPDATE vpn_servers SET is_active = TRUE WHERE id = 11.")
    finally:
        await pool.close()


def main() -> None:
    mode = ""
    args = sys.argv[1:]
    for a in args:
        if a.startswith("--mode="):
            mode = a.split("=", 1)[1]
        elif a in ("--mode",):
            pass
    # also accept positional / --flags without '='
    if not mode:
        for i, a in enumerate(args):
            if a == "--mode" and i + 1 < len(args):
                mode = args[i + 1]
    if not mode:
        print("usage: manage_ru_egress.py --mode "
              "{dump|register-uuid|export|import|apply|repoint|revert|purge-orphans|fix-sniffing|deactivate}",
              file=sys.stderr)
        sys.exit(2)

    if mode == "dump":
        cmd_dump()
    elif mode == "register-uuid":
        cmd_register_uuid()
    elif mode == "purge-orphans":
        cmd_purge_orphans()
    elif mode == "fix-sniffing":
        cmd_fix_sniffing()
    elif mode == "export":
        cmd_export()
    elif mode == "import":
        cmd_import()
    elif mode == "repoint":
        cmd_repoint()
    elif mode == "apply":
        cmd_apply()
    elif mode == "revert":
        cmd_revert()
    elif mode == "deactivate":
        import asyncio

        asyncio.run(cmd_deactivate())
    else:
        print(f"ERROR: unknown mode {mode!r}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
