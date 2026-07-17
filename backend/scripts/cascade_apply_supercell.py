"""Cascade-entry routing: route ALL Supercell domains (Brawl Stars, Clash, Hay Day,
Squad Busters, etc.) to the FOREIGN egress deterministically, so game traffic never
falls into `geoip:ru -> direct` when a Supercell CDN edge resolves to a RU IP.

Problem: cascade-entries are RU boxes (Yandex.Cloud / cloud.ru) using
`domainStrategy: IPIfNonMatch`. Supercell has no explicit rule, so supercell.com etc.
fall through the domain rules; with IPIfNonMatch xray then resolves the domain to an IP
and the trailing `geoip:ru -> direct` rule catches it whenever the CDN serves a RU-edge
IP — egressing the game via RU (throttled/blocked by RKN for some Supercell endpoints =>
lag / "пингует"). This started ~2026-07 (CDN edge rotation / geoip DB update), no repo
routing change involved.

Fix: one explicit `{domain: SUPERCELL_DOMAINS, outboundTag: <foreign-egress>}` rule
inserted high (after geoip:private, before TikTok/foreign + before .ru/geoip:ru).
`domain:X` matches X + all subdomains. Domain rules are evaluated BEFORE IP rules
(IPIfNonMatch), so this short-circuits geoip:ru. The foreign-egress outbound is detected
per-box (the first `vless` outbound = the cascade's foreign relay). Idempotent + backup
+ xray test + restart + verify. Clones the merge pattern of cascade_apply_banks.

Applies to the 3 bare_xray cascade-entries (Германия/Финляндия/Литва). The 4th (США,
89.169.139.153) is a 3x-ui panel — handled separately via panel/xrayTemplateConfig.

Usage: python -m scripts.cascade_apply_supercell            # apply
       python -m scripts.cascade_apply_supercell --dry-run   # show diff, no write
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

# Supercell domains (v2fly domain-list-community/data/supercell + official our-domains).
# `domain:X` matches X and all subdomains. Covers Brawl Stars + all Supercell titles.
SUPERCELL_DOMAINS: list[str] = [
    "domain:supercell.com", "domain:supercell.net", "domain:supercellid.com",
    "domain:supercellgames.com", "domain:supercellcreators.com",
    "domain:supercellstore.com", "domain:supr.cl",
    # Brawl Stars
    "domain:brawlstars.com", "domain:brawlstarsgame.com",
    # Clash family
    "domain:clash.com", "domain:clashofclans.com", "domain:clashroyale.com",
    "domain:clashroyaleapp.com", "domain:clashmini.com",
    # Other titles
    "domain:boombeach.com", "domain:hayday.com", "domain:haydaygame.com",
    "domain:squadbusters.com", "domain:gamesfirsthelsinki.com",
    "domain:seeurlpcl.com", "domain:mo.co",
]

SUPERCELL_DOMAINS_SET = frozenset(SUPERCELL_DOMAINS)
BACKUP_SUFFIX = ".pre-supercell.bak"

# Per-entry SSH keys. cloud.ru boxes share cloudru_key.pem/romtan; Yandex LT entry uses
# the bravada-yandex-new key. Override host/key via env if needed.
_CLOUDRU_KEY = os.environ.get("CLOUDRU_SSH_KEY", "D:/tmp/cloudru_key.pem")
_YANDEX_KEY = os.environ.get("YANDEX_LT_SSH_KEY", "D:/tmp/key_new_vm.pem")


def _foreign_egress_tag(config: dict) -> str | None:
    """The cascade's foreign-egress outbound = the first `vless` outbound (the relay).
    Falls back to the first outbound if none is vless."""
    for o in config.get("outbounds", []):
        if o.get("protocol") == "vless":
            return o.get("tag")
    obs = config.get("outbounds", [])
    return obs[0].get("tag") if obs else None


def merge_supercell_egress(config: dict) -> dict:
    """NON-DESTRUCTIVE, idempotent merge: insert `{domain: SUPERCELL, outboundTag:
    <foreign-egress>}` high in routing.rules (after api / geoip:private), removing any
    prior supercell rule first. Domain rules are matched before IP rules, so this wins
    over the trailing geoip:ru -> direct. Existing rules preserved."""
    cfg = copy.deepcopy(config)
    egress = _foreign_egress_tag(cfg)
    if not egress:
        return cfg  # nothing to route to; leave untouched
    rules = cfg.setdefault("routing", {}).setdefault("rules", [])
    # idempotency: drop any existing rule whose domain set is our supercell set
    rules[:] = [
        r for r in rules
        if set(r.get("domain") or []) != SUPERCELL_DOMAINS_SET
    ]
    insert_at = 0
    for i, r in enumerate(rules):
        ot = r.get("outboundTag")
        if ot == "api" or any("geoip:private" in str(x) for x in (r.get("ip") or [])):
            insert_at = i + 1
    rules[insert_at:insert_at] = [
        {"type": "field", "domain": list(SUPERCELL_DOMAINS), "outboundTag": egress}
    ]
    return cfg


@dataclass
class CascadeEntry:
    name: str
    host: str
    user: str = "romtan"
    key: str = _CLOUDRU_KEY
    config: str = "/usr/local/etc/xray/config.json"


BARE_XRAY_ENTRIES = [
    CascadeEntry("Германия (cloud.ru)", "85.208.87.187", key=_CLOUDRU_KEY),
    CascadeEntry("Финляндия (cloud.ru clr)", "176.108.250.237", key=_CLOUDRU_KEY),
    CascadeEntry("Литва (Yandex)", "89.169.166.161", key=_YANDEX_KEY),
]


def apply_to_entry(entry: CascadeEntry, *, dry_run: bool) -> bool:
    """SSH in, merge supercell rule, write, test, restart, verify. Idempotent."""
    import paramiko

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(entry.host, port=22, username=entry.user, key_filename=entry.key,
                    look_for_keys=False, allow_agent=False, timeout=15)
    except Exception as exc:
        print(f"[{entry.name}] SSH connect FAILED: {exc!r}", file=sys.stderr)
        return False

    def run(cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        _, out, err = cli.exec_command(cmd, timeout=timeout)
        rc = out.channel.recv_exit_status()
        return rc, out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")

    try:
        rc, cur_json, err = run(f"sudo cat {entry.config}")
        if rc != 0:
            print(f"[{entry.name}] read config FAILED rc={rc}: {err.strip()[:200]}", file=sys.stderr)
            return False
        try:
            config = json.loads(cur_json)
        except json.JSONDecodeError as exc:
            print(f"[{entry.name}] config parse FAILED: {exc!r}", file=sys.stderr)
            return False

        egress = _foreign_egress_tag(config)
        merged = merge_supercell_egress(config)
        already = config == merged

        if dry_run:
            print(f"[{entry.name}] DRY-RUN: egress='{egress}' -> {'no-op (rule present)' if already else 'would INSERT supercell rule'}")
            return True
        if already:
            print(f"[{entry.name}] OK (supercell rule already present — no-op)")
            return True

        run(f"sudo cp {entry.config} {entry.config}{BACKUP_SUFFIX}")
        run(f"sudo tee {entry.config} > /dev/null <<'__CFG__'\n{json.dumps(merged, ensure_ascii=False, indent=2)}\n__CFG__")
        rc, _, test_err = run(f"sudo /usr/local/bin/xray run -test -config {entry.config}")
        if rc != 0:
            run(f"sudo cp {entry.config}{BACKUP_SUFFIX} {entry.config}")
            print(f"[{entry.name}] xray test FAILED (restored): {test_err.strip()[:300]}", file=sys.stderr)
            return False
        rc, _, restart_err = run("sudo systemctl restart xray")
        if rc != 0:
            print(f"[{entry.name}] restart rc={rc}: {restart_err.strip()[:200]}", file=sys.stderr)
            return False
        rc, active, _ = run("systemctl is-active xray")
        _, ports, _ = run("sudo ss -lnt | grep -E ':443 ' || true")
        ok = active.strip() == "active" and ":443" in ports
        print(f"[{entry.name}] {'OK' if ok else 'VERIFY-FAIL'}: xray={active.strip()} :443={'up' if ':443' in ports else 'DOWN'}; supercell->{egress}")
        return ok
    finally:
        cli.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="show what would change, do not write")
    args = ap.parse_args()
    ok = True
    for entry in BARE_XRAY_ENTRIES:
        ok = apply_to_entry(entry, dry_run=args.dry_run) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
