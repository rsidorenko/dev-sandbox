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

Runs over SSH on the 4 bare_xray cascade-entries: DE 158.160.188.35 (Yandex, 2026-09-22) + FI 93.77.165.219
(Yandex, 2026-10-03), LT 45.91.52.204 (romtan_key.pem), US 176.109.107.226 (yandex-relay) —
all user romtan. All four are bare_xray now (US was converted from a 3x-ui panel 2026-07-27).

NOTE: routing on these entries was canonicalized in-place on 2026-07-21 (canon_apply.py):
FOREIGN rules precede the RU banks rule. merge_banks_direct re-inserts the rule right after
geoip:private, i.e. it would *reposition* the banks rule above the foreign-relay rules.
Prefer in-place edits (add a domain to the existing banks rule, preserving position) or
re-run canon_apply after. Used here as the authoritative BANK_DOMAINS list + unit-tested merge.

Usage: python -m scripts.cascade_apply_banks            # apply on all 4 bare_xray
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
    "domain:sberbank.ru",
    "domain:sberbank.com",
    "domain:sber.ru",
    "domain:sberbank-online.ru",
    "domain:tinkoff.ru",
    "domain:tinkoff.com",
    "domain:tbank.ru",
    "domain:vtb.ru",
    "domain:vtbdirect.ru",
    "domain:alfabank.ru",
    "domain:alfa-bank.ru",
    "domain:alfabank.com",
    "domain:gpb.ru",
    "domain:gazprombank.ru",
    "domain:raiffeisen.ru",
    "domain:raif.ru",
    "domain:mkb.ru",
    "domain:psbank.ru",
    "domain:open.ru",
    "domain:rosbank.ru",
    "domain:homecredit.ru",
    "domain:sovkombank.ru",
    "domain:otpbank.ru",
    "domain:pochtabank.ru",
    "domain:pochta.ru",
    "domain:mtsbank.ru",
    "domain:bcs.ru",
    "domain:ubrr.ru",
    "domain:tochka.com",
    "domain:tochka.ru",
    "domain:avangard.ru",
    "domain:bspb.ru",
    "domain:unicreditbank.ru",
    "domain:bank-zenit.ru",
    "domain:rencredit.ru",
    "domain:ozonbank.ru",
    # --- payment systems / fintech ---
    "domain:yoomoney.ru",
    "domain:money.yandex.ru",
    "domain:qiwi.com",
    "domain:qiwi.ru",
    "domain:mir.ru",
    "domain:mirpay.ru",
    "domain:mironline.ru",
    "domain:nspk.ru",  # covers sbp.nspk.ru (NSPK / SBP)
    "domain:gosuslugi.ru",
    "domain:troika.ru",
    "domain:mos.ru",  # transport.mos.ru covered by mos.ru
    # --- anti-fraud / security vendors bank apps phone home to (non-.ru) ---
    # All *.ru bank domains already egress via direct via regexp:.*\.ru$ / geoip:ru below;
    # the banks rule's real value is these non-.ru endpoints banks' sites/apps contact.
    "domain:bi.zone",  # BI.ZONE (ex-Сбер subsidiary) AntiFraud/WAF — protects bank apps + payments
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
        r for r in rules if not (r.get("outboundTag") == "direct" and set(r.get("domain") or []) == BANK_DOMAINS_SET)
    ]
    insert_at = 0
    for i, r in enumerate(rules):
        ot = r.get("outboundTag")
        if ot == "api" or any("geoip:private" in str(x) for x in (r.get("ip") or [])):
            insert_at = i + 1
    rules[insert_at:insert_at] = [{"type": "field", "domain": list(BANK_DOMAINS), "outboundTag": "direct"}]
    return cfg


@dataclass
class CascadeEntry:
    name: str
    host: str
    user: str = "romtan"
    key: str = DEFAULT_SSH_KEY
    config: str = "/usr/local/etc/xray/config.json"


BARE_XRAY_ENTRIES = [
    # The 4 cascade-entries (all RU servers: Yandex.Cloud / cloud.ru), each does split-routing
    # (RU bank/services -> direct RU egress, rest -> foreign relay). Labeled by egress country.
    # YouTube-only (158.160.82.113) and CDN-bypass (158.160.234.239) are NOT bank-routing targets.
    # All four are bare_xray now (US converted from x-ui on 2026-07-27).
    CascadeEntry("DE cascade-entry (Frankfurt egress)", "158.160.188.35"),
    CascadeEntry("FI cascade-entry (Helsinki egress)", "93.77.165.219"),
    CascadeEntry("LT cascade-entry (Lithuania egress)", "45.91.52.204", key="D:/tmp/romtan_key.pem"),
    CascadeEntry("US cascade-entry (US egress)", "176.109.107.226", key="D:/TelegramBotVPN/.ssh/yandex-relay"),
]


def apply_to_entry(entry: CascadeEntry, *, dry_run: bool) -> bool:
    """SSH into the cascade-entry, merge the banks rule, write, test, restart, verify.
    Returns True on success. Idempotent (no-op if rule already present)."""
    import paramiko

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(
            entry.host,
            port=22,
            username=entry.user,
            key_filename=entry.key,
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
        )
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

        from scripts.canonicalize_routing import apply_remote_config

        if not apply_remote_config(cli, merged, config_path=entry.config):
            print(f"[{entry.name}] обновление конфигурации не завершено", file=sys.stderr)
            return False
        rc, active, _ = run("systemctl is-active xray")
        _, ports, _ = run("sudo ss -lnt | grep -E ':443 ' || true")
        ok = active.strip() == "active" and ":443" in ports
        print(
            f"[{entry.name}] {'OK' if ok else 'VERIFY-FAIL'}: xray={active.strip()} :443={'up' if ':443' in ports else 'DOWN'}; banks rule inserted"
        )
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
