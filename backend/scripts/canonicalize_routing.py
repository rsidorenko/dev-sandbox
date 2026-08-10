"""Canonical xray routing for the RU split-routing servers (cascade-entries + CDN origins).

Each RU server does split-routing: Russian services egress via `direct` (freedom = the box's
own RU IP — the "RU leg"), everything else via a foreign relay outbound (Frankfurt / Helsinki /
Lithuania / US / Albania / CDN-exit). Without explicit domain rules, RU services on non-.ru
TLDs or foreign-IP CDNs fall through to the foreign relay -> anti-fraud blocks/logouts
(banks, VK video CDN on a German IP, VK media on Cloudflare, etc.).

This tool rebuilds `routing.rules` to a single canonical order from EXPLICIT domain constants
(the source of truth), so it works on any server regardless of current state — including a
sparse / pass-through box whose routing was previously empty (where a classify-from-existing
approach would have nothing to classify). Per-server differences are auto-detected from the
live config: the foreign egress outbound tag, whether an `api` inbound exists, and any
inbound-tagged trailing rules to preserve (e.g. LT tiktok-split, US in-443/8443, YT yt-inbound).

Canonical order:
    [api]                                  # only if an api inbound exists
    geoip:private -> direct
    FOREIGN -> egress :  supercell, tiktok, ggsel, griffiny
    RU     -> direct :  banks, vk, max, yandex, ru_services
    regexp:.*\\.ru$           -> direct
    domain:ru,su,рф(xn--p1ai) -> direct
    geoip:ru                 -> direct
    <preserved inbound-tagged trailing rules>

`domain:X` in xray matches X and all subdomains, so `domain:vk.com` covers login/id/oauth/
api/static/m.vk.com (the whole auth path). BANK_DOMAINS is owned by cascade_apply_banks
(imported here to stay DRY); the other families are defined here.

Before restart, live gRPC users (in-memory on bare_xray) are persisted into
settings.clients via HandlerService/GetInboundUsers, so the restart does not drop clients.
A geoip.dat pre-flight check warns + aborts if the box lacks geodata (canonical rules need
geoip:private / geoip:ru — a pass-through server freshly converted will not have it). Then
backup -> write -> `xray run -test` (auto-restore on failure) -> restart -> verify.

Targets (7 RU split-routing servers, verified 2026-08-10): 4 cascade-entries DE/FI/LT/US,
the YouTube-ad-free entry YT, and the two CDN-bypass origins CDN-LT / CDN-DE. All bare_xray.

Usage:
    python -m scripts.canonicalize_routing               # apply on all targets
    python -m scripts.canonicalize_routing --dry-run     # show planned rules, no write
    python -m scripts.canonicalize_routing --entry YT    # only one target (by name substring)
    python -m scripts.canonicalize_routing --list        # list targets and exit
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import logging
import os
import re
import sys
from dataclasses import dataclass

# BANK_DOMAINS is the authoritative bank/payment list, owned by cascade_apply_banks.
from scripts.cascade_apply_banks import BANK_DOMAINS

_LOGGER = logging.getLogger(__name__)

# --- canonical domain families (the source of truth; `domain:X` matches X + subdomains) ---

# VKontakte: site, app, auth (subdomains of vk.com/vk.ru), media CDNs.
# vkvideo.com -> DE/SEDO (foreign), vkuserapi.com -> Cloudflare (foreign),
# vkcompany.com -> AWS, vk-cdn.com -> US: these MUST be explicit or they leak past geoip:ru.
VK_DOMAINS: list[str] = [
    "domain:vk.com", "domain:vk.ru", "domain:vk.me", "domain:vk.org", "domain:vk.cc",
    "domain:vkontakte.ru", "domain:vkontakte.com", "domain:vk.link", "domain:vk.design",
    "domain:vk.company", "domain:vkcompany.com", "domain:vk-portal.net",
    "domain:userapi.com", "domain:vkuserapi.com", "domain:mycdn.me", "domain:vk-cdn.com",
    "domain:vkuservideo.net", "domain:vkuservideo.com", "domain:vkuservideo.ru",
    "domain:vkuseraudio.net", "domain:vkuseraudio.com", "domain:vkuseraudio.ru",
    "domain:vkuserlive.net", "domain:vkuserlive.com", "domain:vkuserlive.ru",
    "domain:vk.video", "domain:vkvideo.ru", "domain:vkvideo.com",
    "domain:mvk.ru", "domain:mvk.com",
]

# Supercell games (Brawl Stars / Clash / Hay Day ...) -> foreign egress (game servers abroad).
SUPERCELL_DOMAINS: list[str] = [
    "domain:boombeach.com", "domain:brawlstars.com", "domain:brawlstarsgame.com",
    "domain:clash.com", "domain:clashmini.com", "domain:clashofclans.com",
    "domain:clashroyale.com", "domain:clashroyaleapp.com", "domain:gamesfirsthelsinki.com",
    "domain:hayday.com", "domain:haydaygame.com", "domain:mo.co", "domain:seeurlpcl.com",
    "domain:squadbusters.com", "domain:supercell.com", "domain:supercell.net",
    "domain:supercellcreators.com", "domain:supercellgames.com", "domain:supercellid.com",
    "domain:supercellstore.com", "domain:supr.cl",
]

# ByteDance / TikTok -> foreign egress.
TIKTOK_DOMAINS: list[str] = [
    "domain:bytefcdn-oversea.com", "domain:byteoversea.com", "domain:ibytedtos.com",
    "domain:ibyteimg.com", "domain:musical.ly", "domain:tiktok.com", "domain:tiktok.ru",
    "domain:tiktokcdn.com", "domain:tiktokv.com",
]

# ggsel + 2ip (store / IP-info) -> foreign egress.
GGSEL_DOMAINS: list[str] = [
    "domain:2ip.io", "domain:2ip.ru", "domain:ggsel.cc", "domain:ggsel.com",
    "domain:ggsel.info", "domain:ggsel.io", "domain:ggsel.me", "domain:ggsel.net",
    "domain:ggsel.org", "domain:ggsel.pro", "domain:ggsel.ru",
]

GRIFFINY_DOMAINS: list[str] = ["domain:griffiny.ru"]

MAX_DOMAINS: list[str] = ["domain:max.ru", "domain:oneme.ru"]

YANDEX_DOMAINS: list[str] = ["domain:yandex.com", "domain:yandex.net", "domain:yastatic.net"]

# Russian services on non-.ru TLDs (the .ru ones are already caught by regexp/geoip below);
# listing them makes egress deterministic regardless of DNS.
RU_SERVICES_DOMAINS: list[str] = [
    "domain:2gis.com", "domain:avito.com", "domain:avito.st", "domain:beget.com",
    "domain:bookmate.com", "domain:cdnvideohub.com", "domain:delivery-club.com",
    "domain:dodois.com", "domain:dodois.io", "domain:dodopizza.com", "domain:dodostatic.net",
    "domain:eadaily.com", "domain:fix-price.com", "domain:habr.com", "domain:habracdn.net",
    "domain:ivi.tv", "domain:jetbrains.com", "domain:kaspersky-labs.com", "domain:kaspersky.com",
    "domain:kinorium.com", "domain:lenta.com", "domain:litres.com", "domain:moex.com",
    "domain:netmonet.co", "domain:ozon.com", "domain:ozon.dev", "domain:ozon.tech",
    "domain:ozon.travel", "domain:ozonusercontent.com", "domain:promodj.com", "domain:rt.com",
    "domain:sputniknews.com", "domain:t1.cloud", "domain:timeweb.cloud", "domain:timeweb.com",
    "domain:tips.tips", "domain:trbcdn.net", "domain:wildberries.com",
]

# Foreign families route to the egress relay; RU families route to direct.
_FOREIGN_FAMILIES = [SUPERCELL_DOMAINS, TIKTOK_DOMAINS, GGSEL_DOMAINS, GRIFFINY_DOMAINS]
_RU_FAMILIES = [BANK_DOMAINS, VK_DOMAINS, MAX_DOMAINS, YANDEX_DOMAINS, RU_SERVICES_DOMAINS]

BACKUP_SUFFIX = ".pre-canon.bak"
GEOIP_PATH = "/usr/local/share/xray/geoip.dat"
XRAY_BIN = "/usr/local/bin/xray"
CONFIG_PATH = "/usr/local/etc/xray/config.json"
GRPC_API = "localhost:10085"

DEFAULT_SSH_KEY = os.environ.get("CLOUDRU_SSH_KEY", "D:/tmp/cloudru_key.pem")
DEFAULT_SSH_USER = "romtan"

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def detect_egress_tag(config: dict) -> str:
    """First vless outbound tag = the foreign relay this server cascades to.

    Every split-routing server has exactly one vless relay outbound (Frankfurt / Helsinki /
    Lithuania / US / Albania / to-exit). Raises ValueError if none — canonical routing needs
    a known egress to point the FOREIGN rules at.
    """
    for ob in config.get("outbounds", []):
        if ob.get("protocol") == "vless":
            return ob.get("tag")
    raise ValueError("no vless outbound found — cannot determine foreign egress tag")


def build_canonical_rules(
    *, egress_tag: str, has_api: bool, trailing_rules: list[dict] | None = None
) -> list[dict]:
    """Pure builder: returns the canonical routing.rules list for one server.

    Deterministic from the module constants + the per-server egress_tag / has_api / trailing
    inbound-tagged rules. Does not touch the filesystem or the network.
    """
    rules: list[dict] = []
    if has_api:
        rules.append({"type": "field", "inboundTag": ["api"], "outboundTag": "api"})
    rules.append({"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"})
    rules.extend({"type": "field", "domain": list(fam), "outboundTag": egress_tag}
                 for fam in _FOREIGN_FAMILIES)
    rules.extend({"type": "field", "domain": list(fam), "outboundTag": "direct"}
                 for fam in _RU_FAMILIES)
    rules.append({"type": "field", "domain": ["regexp:.*\\.ru$"], "outboundTag": "direct"})
    rules.append(
        {"type": "field", "domain": ["domain:ru", "domain:su", "domain:xn--p1ai"],
         "outboundTag": "direct"}
    )
    rules.append({"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"})
    if trailing_rules:
        rules.extend(copy.deepcopy(trailing_rules))
    return rules


def rebuild_config_routing(config: dict) -> dict:
    """Return a deep copy of `config` with routing.rules rebuilt to canonical order.

    Auto-detects from the live config: foreign egress tag, whether an `api` inbound exists,
    and any inbound-tagged rules to preserve (e.g. per-inbound foreign defaults / splits).
    Does not mutate the input.
    """
    cfg = copy.deepcopy(config)
    egress_tag = detect_egress_tag(cfg)
    has_api = any(ib.get("tag") == "api" for ib in cfg.get("inbounds", []))
    trailing = [
        r for r in cfg.get("routing", {}).get("rules", [])
        if r.get("inboundTag") and r.get("inboundTag") != ["api"]
    ]
    cfg.setdefault("routing", {})["rules"] = build_canonical_rules(
        egress_tag=egress_tag, has_api=has_api, trailing_rules=trailing
    )
    cfg["routing"]["domainStrategy"] = "IPIfNonMatch"
    return cfg


def parse_grpc_users(grpc_json: str) -> list[dict]:
    """Parse HandlerService/GetInboundUsers JSON into xray settings.clients entries
    ({email, id, flow}). Pure + unit-tested; the gRPC call itself lives in persist_users."""
    data = json.loads(grpc_json or "{}")
    out: list[dict] = []
    for user in data.get("users", []):
        email = user.get("email", "")
        account = (user.get("account") or {}).get("value", "") or ""
        try:
            raw = base64.b64decode(account)
        except Exception:
            raw = b""
        m = _UUID_RE.search(raw.decode("latin1", "ignore"))
        uuid = m.group(0) if m else ""
        if not uuid:
            continue
        flow = "xtls-rprx-vision" if b"xtls-rprx-vision" in raw else ""
        out.append({"email": email, "id": uuid, "flow": flow})
    return out


@dataclass
class RoutingTarget:
    name: str
    host: str
    user: str = DEFAULT_SSH_USER
    key: str = DEFAULT_SSH_KEY
    config: str = CONFIG_PATH


TARGETS: list[RoutingTarget] = [
    RoutingTarget("DE", "85.208.87.57"),                       # cloud.ru, egress Frankfurt
    RoutingTarget("FI", "85.208.85.117"),                      # cloud.ru, egress Helsinki
    RoutingTarget("LT", "158.160.80.40",                       # Yandex, egress Lithuania
                  key="C:/Users/Roman/.ssh/yandex_new_key"),
    RoutingTarget("US", "89.169.139.153",                      # Yandex, egress US
                  key="D:/TelegramBotVPN/.ssh/yandex-relay"),
    RoutingTarget("YT", "158.160.82.113",                      # Yandex, YouTube-ad-free, egress Albania
                  key="C:/Users/Roman/.ssh/yt_new_key.pem"),
    RoutingTarget("CDN-LT", "158.160.40.11",                   # CDN-bypass origin, egress Lithuania-exit
                  key="C:/Users/Roman/.ssh/yandex_new_key"),
    RoutingTarget("CDN-DE", "158.160.234.239",                 # CDN-bypass origin, egress Frankfurt-exit
                  key="D:/tmp/origin_new_key"),
]


def _run(cli, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return rc, out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")


def apply_to_target(target: RoutingTarget, *, dry_run: bool) -> bool:
    """SSH into the target, persist users, rebuild routing to canonical, write, test, restart.

    Safe by construction: persist-users before restart (no client wipe), geoip pre-flight,
    backup, `xray run -test` gate with auto-restore on failure, verify after restart.
    Returns True on success. Idempotent (a canonical box is rewritten to the same rules)."""
    import paramiko

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(target.host, port=22, username=target.user, key_filename=target.key,
                    look_for_keys=False, allow_agent=False, timeout=15)
    except Exception as exc:
        print(f"[{target.name}] SSH connect FAILED: {exc!r}", file=sys.stderr)
        return False

    try:
        rc, cur_json, err = _run(cli, f"sudo cat {target.config}")
        if rc != 0:
            print(f"[{target.name}] read config FAILED rc={rc}: {err.strip()[:200]}", file=sys.stderr)
            return False
        try:
            config = json.loads(cur_json)
        except json.JSONDecodeError as exc:
            print(f"[{target.name}] config parse FAILED: {exc!r}", file=sys.stderr)
            return False

        try:
            rebuilt = rebuild_config_routing(config)
        except ValueError as exc:
            print(f"[{target.name}] cannot canonicalize: {exc}", file=sys.stderr)
            return False

        if dry_run:
            egress = detect_egress_tag(config)
            has_api = any(ib.get("tag") == "api" for ib in config.get("inbounds", []))
            same = config.get("routing", {}).get("rules") == rebuilt["routing"]["rules"] and \
                config.get("routing", {}).get("domainStrategy") == "IPIfNonMatch"
            print(f"[{target.name}] DRY-RUN: egress={egress} api={has_api} "
                  f"rules={len(rebuilt['routing']['rules'])} "
                  f"({'no-op (already canonical)' if same else 'WOULD REBUILD'})")
            return True

        # geoip pre-flight: canonical rules use geoip:private / geoip:ru.
        rc, _, _ = _run(cli, f"sudo test -f {GEOIP_PATH} && echo ok")
        if rc != 0:
            print(f"[{target.name}] ABORT: {GEOIP_PATH} missing — canonical rules need geoip. "
                  f"Install first:\n"
                  f"  sudo mkdir -p {os.path.dirname(GEOIP_PATH)} && "
                  f"sudo curl -L -o {GEOIP_PATH} "
                  f"https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat",
                  file=sys.stderr)
            return False

        # persist live gRPC users into settings.clients so the restart does not drop them.
        persisted, persist_failed = _persist_users(cli, config)
        if persist_failed:
            print(f"[{target.name}] ABORT: gRPC persist failed on {persist_failed} inbound(s); "
                  f"no write, no restart", file=sys.stderr)
            return False
        # re-derive canonical on the config that now carries persisted clients
        rebuilt = rebuild_config_routing(config)

        # backup + write
        _run(cli, f"sudo cp {target.config} {target.config}{BACKUP_SUFFIX}")
        _run(cli, f"sudo tee {target.config} > /dev/null <<'__CFG__'\n"
                  f"{json.dumps(rebuilt, ensure_ascii=False, indent=2)}\n__CFG__")

        # test (auto-restore on failure)
        rc, _, test_err = _run(cli, f"sudo {XRAY_BIN} run -test -config {target.config}")
        if rc != 0:
            _run(cli, f"sudo cp {target.config}{BACKUP_SUFFIX} {target.config}")
            print(f"[{target.name}] xray test FAILED (restored backup): {test_err.strip()[:300]}",
                  file=sys.stderr)
            return False

        # restart + verify
        _run(cli, "sudo systemctl restart xray")
        rc, active, _ = _run(cli, "systemctl is-active xray")
        vk = next((r for r in rebuilt["routing"]["rules"]
                   if "domain:vk.com" in (r.get("domain") or [])), {})
        vk_ok = (active.strip() == "active" and vk.get("outboundTag") == "direct"
                 and "domain:vkvideo.com" in (vk.get("domain") or []))
        print(f"[{target.name}] {'OK' if vk_ok else 'VERIFY-WARN'}: xray={active.strip()} "
              f"rules={len(rebuilt['routing']['rules'])} persisted={persisted} "
              f"VK->direct dom={len(vk.get('domain') or [])}")
        return vk_ok
    finally:
        cli.close()


def _persist_users(cli, config: dict) -> tuple[int, int]:
    """For each vless inbound, pull live users via gRPC GetInboundUsers and write them into
    settings.clients (mutates `config` in place). Returns (users_persisted, inbounds_failed).
    A failed inbound aborts the whole apply (no partial wipe)."""
    persisted = 0
    failed = 0
    for ib in config.get("inbounds", []):
        if ib.get("protocol") != "vless" or ib.get("tag") == "api":
            continue
        tag = ib.get("tag")
        req = json.dumps({"tag": tag})
        rc, out, _ = _run(
            cli,
            f"sudo grpcurl -plaintext -d {json.dumps(req)!r} {GRPC_API} "
            f"xray.app.proxyman.command.HandlerService/GetInboundUsers",
            timeout=15,
        )
        if rc != 0 or not out.strip():
            failed += 1
            continue
        clients = parse_grpc_users(out)
        ib.setdefault("settings", {})["clients"] = clients
        persisted += len(clients)
    return persisted, failed


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show planned canonical rules, no write")
    ap.add_argument("--entry", default="", help="apply only to targets whose name contains this")
    ap.add_argument("--list", action="store_true", help="list targets and exit")
    args = ap.parse_args()

    if args.list:
        for t in TARGETS:
            print(f"{t.name:8} {t.host}")
        return 0

    targets = [t for t in TARGETS if args.entry.lower() in t.name.lower()]
    if not targets:
        print(f"no target matches --entry '{args.entry}'", file=sys.stderr)
        return 2

    ok = True
    for target in targets:
        ok = apply_to_target(target, dry_run=args.dry_run) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
