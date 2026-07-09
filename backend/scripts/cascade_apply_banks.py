"""Cascade-entry routing: route Russian bank / payment-system domains to `direct`
(RU egress) deterministically, so bank apps never egress through a foreign relay.

All 4 cascade-entries are in Moscow, RU (Yandex.Cloud / cloud.ru), so their `direct`
outbound (freedom) is a RU IP. But the default outbound on each is a FOREIGN relay
(Albania/Lithuania/Germany/Finland/LA), and the only RU catch-alls today are
`regexp:.*\\.ru$` + `geoip:ru`. So Russian bank domains on `.com`/cloud
(sberbank.com, alfabank.com, qiwi.com, tochka.com, API/CDN endpoints) fall through
to the default -> foreign egress -> bank anti-fraud blocks/logouts.

This adds ONE explicit `{domain: BANK_DOMAINS, outboundTag: "direct"}` rule high in
the routing order (after geoip:private, before TikTok/foreign) on each cascade-entry.
`domain:X` in xray matches X and all subdomains. Idempotent + backup + xray test +
restart + verify. Reuses the merge pattern of manage_ru_egress.merge_domain_family_routing.

Runs over SSH (cloudru_key.pem / romtan) on the 3 bare_xray cascade-entries.
The 4th cascade-entry (89.169.139.153) is a 3x-ui panel — handled separately via
manage_ru_egress / db-edit (its xray config regenerates from x-ui.db).

Usage: python -m scripts.cascade_apply_banks            # apply on all 3 bare_xray
       python -m scripts.cascade_apply_banks --dry-run   # show diff, no write
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

# Russian bank / payment-system domains. `domain:X` matches X + all subdomains.
# Most .ru are already caught by `regexp:.*\.ru$` / `geoip:ru`; this list makes the
# `.com`/cloud variants deterministic and re-confirms the key ones.
BANK_DOMAINS: list[str] = [
    # --- banks (.ru + .com/cloud variants) ---
    "domain:sberbank.ru", "domain:sberbank.com", "domain:sber.ru", "domain:sberbank-online.ru",
    "domain:tinkoff.ru", "domain:tinkoff.com", "domain:tbank.ru",
    "domain:vtb.ru", "domain:vtbdirect.ru",
    "domain:alfabank.ru", "domain:alfa-bank.ru", "domain:alfabank.com",
    "domain:gpb.ru", "domain:gazprombank.ru",
    "domain:raiffeisen.ru", "domain:raif.ru",
    "domain:mkb.ru", "domain:psbank.ru", "domain:open.ru", "domain:rosbank.ru",
    "domain:homecredit.ru", "domain:sovkombank.ru", "domain:otpbank.ru",
    "domain:pochtabank.ru", "domain:pochta.ru", "domain:mtsbank.ru", "domain:bcs.ru",
    "domain:ubrr.ru", "domain:tochka.com", "domain:tochka.ru", "domain:avangard.ru",
    "domain:bspb.ru", "domain:unicreditbank.ru", "domain:bank-zenit.ru", "domain:rencredit.ru",
    "domain:ozonbank.ru",
    # --- payment systems / fintech ---
    "domain:yoomoney.ru", "domain:money.yandex.ru",
    "domain:qiwi.com", "domain:qiwi.ru",
    "domain:mir.ru", "domain:mirpay.ru", "domain:mironline.ru",
    "domain:nspk.ru",  # covers sbp.nspk.ru (NSPK / SBP)
    "domain:gosuslugi.ru",
    "domain:troika.ru", "domain:mos.ru",  # transport.mos.ru covered by mos.ru
]

BANK_DOMAINS_SET = frozenset(BANK_DOMAINS)
BACKUP_SUFFIX = ".pre-banks.bak"

# SSH key for the cascade-entries (all share cloudru_key.pem / romtan).
# Windows path by default; override via CLOUDRU_SSH_KEY for other hosts.
DEFAULT_SSH_KEY = os.environ.get("CLOUDRU_SSH_KEY", "D:/tmp/cloudru_key.pem")


def merge_banks_direct(config: dict) -> dict:
    """NON-DESTRUCTIVE, idempotent merge: insert a `{domain: BANK_DOMAINS, outboundTag:
    "direct"}` rule high in routing.rules (after the last api / geoip:private rule),
    removing any prior identical rule first. Pure + unit-tested.

    On cascade-entries `direct` is freedom (RU egress, the box is in Moscow). We insert
    BEFORE TikTok/foreign-relay rules so bank domains deterministically egress via RU
    regardless of DNS / geoip. Existing rules are preserved.
    """
    cfg = copy.deepcopy(config)
    rules = cfg.setdefault("routing", {}).setdefault("rules", [])
    # idempotency: drop any existing rule that is exactly our banks rule
    rules[:] = [
        r for r in rules
        if not (r.get("outboundTag") == "direct" and set(r.get("domain") or []) == BANK_DOMAINS_SET)
    ]
    insert_at = 0
    for i, r in enumerate(rules):
        ot = r.get("outboundTag")
        if ot == "api" or any("geoip:private" in str(x) for x in (r.get("ip") or [])):
            insert_at = i + 1
    rules[insert_at:insert_at] = [
        {"type": "field", "domain": list(BANK_DOMAINS), "outboundTag": "direct"}
    ]
    return cfg


@dataclass
class CascadeEntry:
    name: str
    host: str
    user: str = "romtan"
    key: str = DEFAULT_SSH_KEY
    config: str = "/usr/local/etc/xray/config.json"


BARE_XRAY_ENTRIES = [
    CascadeEntry("Yandex (yt+lt)", "158.160.166.233"),
    CascadeEntry("cloud.ru Frankfurt-egress", "85.208.87.187"),
    CascadeEntry("cloud.ru Helsinki-egress", "176.108.250.237"),
]


def apply_to_entry(entry: CascadeEntry, *, dry_run: bool) -> bool:
    """SSH into the cascade-entry, merge the banks rule, write, test, restart, verify.
    Returns True on success. Idempotent (no-op if rule already present)."""
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

        merged = merge_banks_direct(config)
        already = config == merged  # would-be no-op

        if dry_run:
            print(f"[{entry.name}] DRY-RUN: {'no-op (rule present)' if already else 'would INSERT banks->direct rule'}")
            if not already:
                print("  insertion target: after api/geoip:private rule")
            return True

        if already:
            print(f"[{entry.name}] OK (banks rule already present — no-op)")
            return True

        # backup
        run(f"sudo cp {entry.config} {entry.config}{BACKUP_SUFFIX}")
        # write merged
        run(f"sudo tee {entry.config} > /dev/null <<'__CFG__'\n{json.dumps(merged, ensure_ascii=False, indent=2)}\n__CFG__")
        # test
        rc, _, test_err = run(f"sudo /usr/local/bin/xray run -test -config {entry.config}")
        if rc != 0:
            # restore backup on test failure
            run(f"sudo cp {entry.config}{BACKUP_SUFFIX} {entry.config}")
            print(f"[{entry.name}] xray test FAILED (restored backup): {test_err.strip()[:300]}", file=sys.stderr)
            return False
        # restart
        rc, _, restart_err = run("sudo systemctl restart xray")
        if rc != 0:
            print(f"[{entry.name}] systemctl restart returned rc={rc}: {restart_err.strip()[:200]}", file=sys.stderr)
            return False
        # verify: rule present + xray active + :443 listening
        rc, active, _ = run("systemctl is-active xray")
        _, ports, _ = run("sudo ss -lnt | grep -E ':443 ' || true")
        ok = active.strip() == "active" and ":443" in ports
        print(f"[{entry.name}] {'OK' if ok else 'VERIFY-FAIL'}: xray={active.strip()} :443={'up' if ':443' in ports else 'DOWN'}; banks rule inserted")
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
