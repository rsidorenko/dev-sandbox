"""Unit tests for the pure routing builders in scripts.canonicalize_routing.

These cover build/detect/parse logic WITHOUT touching the network. The SSH apply path
(persist-users, backup, xray test, restart) is operator-only and exercised on live boxes.
"""

from __future__ import annotations

import base64
import copy
import json

import pytest
from scripts.canonicalize_routing import (
    BANK_DOMAINS,  # re-exported sanity check
    GGSEL_DOMAINS,
    GRIFFINY_DOMAINS,
    MAX_DOMAINS,
    MAX_IPCHECK_DOMAINS,
    RU_SERVICES_DOMAINS,
    SUPERCELL_DOMAINS,
    TELEMOST_DOMAINS,
    TG_EGRESS_DOMAINS,
    TG_EGRESS_IPS,
    TIKTOK_DOMAINS,
    VK_DOMAINS,
    YANDEX_DOMAINS,
    YT_DIRECT_DOMAINS,
    build_canonical_rules,
    detect_egress_tag,
    parse_grpc_users,
    rebuild_config_routing,
)
from scripts.cascade_apply_banks import BANK_DOMAINS as REPO_BANK_DOMAINS

# --- fixtures -----------------------------------------------------------------


def _cfg(egress_tag="to-exit", *, has_api=True, extra_rules=None):
    """Minimal split-routing config: freedom(direct) + vless(egress), one vless inbound."""
    inbounds = []
    if has_api:
        inbounds.append({"tag": "api", "listen": "127.0.0.1", "port": 10085, "protocol": "dokodemo-door"})
    inbounds.append({"tag": "vless-in", "port": 443, "protocol": "vless", "settings": {"clients": []}})
    return {
        "inbounds": inbounds,
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": egress_tag, "protocol": "vless", "settings": {"vnext": []}},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": list(extra_rules or []),
        },
    }


# --- constants ----------------------------------------------------------------


def test_bank_domains_match_repo_authority():
    """canonicalize must reuse cascade_apply_banks' bank list (single source of truth)."""
    assert BANK_DOMAINS is REPO_BANK_DOMAINS
    assert len(BANK_DOMAINS) >= 40  # known families are non-trivial


@pytest.mark.parametrize(
    "fam,min_count,marker",
    [
        (VK_DOMAINS, 30, "domain:vkvideo.com"),
        (VK_DOMAINS, 30, "domain:vkuserapi.com"),
        (SUPERCELL_DOMAINS, 20, "domain:supercell.com"),
        (TIKTOK_DOMAINS, 9, "domain:tiktok.com"),
        (GGSEL_DOMAINS, 11, "domain:ggsel.com"),
        (GRIFFINY_DOMAINS, 1, "domain:griffiny.ru"),
        (MAX_DOMAINS, 4, "domain:max.ru"),
        (MAX_DOMAINS, 4, "domain:okcdn.ru"),
        (MAX_IPCHECK_DOMAINS, 6, "domain:api.ipify.org"),
        (YANDEX_DOMAINS, 3, "domain:yandex.com"),
        (TELEMOST_DOMAINS, 1, "domain:telemost.com"),
        (RU_SERVICES_DOMAINS, 30, "domain:ozon.com"),
        (TG_EGRESS_DOMAINS, 10, "domain:telegram.org"),
        (TG_EGRESS_DOMAINS, 10, "domain:t.me"),
    ],
)
def test_family_nonempty_with_marker(fam, min_count, marker):
    assert len(fam) >= min_count
    assert all(d.startswith("domain:") for d in fam)
    assert marker in fam
    assert len(fam) == len(set(fam))  # no dupes within a family


def test_tg_egress_ips_are_cidrs():
    """TG DC ranges (NL/SE) — geoip:ru never matches them, the ip-rule steers the apps."""
    assert len(TG_EGRESS_IPS) >= 7
    assert all("/" in ip for ip in TG_EGRESS_IPS)
    assert "149.154.160.0/20" in TG_EGRESS_IPS and "91.108.4.0/22" in TG_EGRESS_IPS
    assert len(TG_EGRESS_IPS) == len(set(TG_EGRESS_IPS))


def test_no_domain_appears_in_two_families():
    """A domain in two families is ambiguous routing — fail if anyone adds a dupe."""
    all_domains = (
        SUPERCELL_DOMAINS
        + TIKTOK_DOMAINS
        + GGSEL_DOMAINS
        + GRIFFINY_DOMAINS
        + BANK_DOMAINS
        + VK_DOMAINS
        + MAX_DOMAINS
        + MAX_IPCHECK_DOMAINS
        + YANDEX_DOMAINS
        + TELEMOST_DOMAINS
        + RU_SERVICES_DOMAINS
        + TG_EGRESS_DOMAINS
    )
    assert len(all_domains) == len(set(all_domains))


# --- build_canonical_rules ----------------------------------------------------


def test_canonical_order_with_api():
    rules = build_canonical_rules(egress_tag="to-exit", has_api=True)
    # 1 api + 1 geoip:private + 4 foreign + 7 RU + regexp + tld + geoip:ru = 16
    assert len(rules) == 16
    assert rules[0] == {"type": "field", "inboundTag": ["api"], "outboundTag": "api"}
    assert rules[1] == {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"}
    assert rules[-1] == {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"}


def test_canonical_order_without_api():
    rules = build_canonical_rules(egress_tag="egress", has_api=False)
    assert not any(r.get("inboundTag") == ["api"] for r in rules)
    # api dropped -> 15 rules
    assert len(rules) == 15
    assert rules[0] == {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"}


def test_foreign_families_route_to_egress():
    rules = build_canonical_rules(egress_tag="to-exit", has_api=False)
    egress_rules = [r for r in rules if r.get("outboundTag") == "to-exit"]
    # supercell, tiktok, ggsel, griffiny
    assert len(egress_rules) == 4
    flat = {d for r in egress_rules for d in (r.get("domain") or [])}
    assert "domain:brawlstars.com" in flat
    assert "domain:tiktok.com" in flat
    assert "domain:ggsel.com" in flat
    assert "domain:griffiny.ru" in flat
    # 2026-08-20: MAX + its ip-check domains moved back to direct (RU leg) —
    # one fleet-wide policy, experiments stopped (see MAX_DOMAINS comment).
    assert "domain:max.ru" not in flat
    assert "domain:api.ipify.org" not in flat
    # VK must NOT be on the egress leg — it leaks to foreign anti-fraud
    assert not any(d.startswith("domain:vk") for d in flat)
    assert not any(d.startswith("domain:userapi") for d in flat)


def test_ru_families_route_to_direct():
    rules = build_canonical_rules(egress_tag="to-exit", has_api=False)
    direct_domain_rules = [r for r in rules if r.get("outboundTag") == "direct" and r.get("domain")]
    flat = {d for r in direct_domain_rules for d in r["domain"]}
    # banks + vk + yandex + ru_services + max + max-ipcheck all -> direct (2026-08-20)
    assert "domain:sberbank.ru" in flat
    assert "domain:vk.com" in flat
    assert "domain:vkvideo.com" in flat  # foreign-IP VK CDN must be explicit-direct
    assert "domain:vkuserapi.com" in flat  # Cloudflare-IP VK media
    assert "domain:yandex.com" in flat
    # Телемост: telemost.com на AWS GA (foreign IP) — geoip:ru не матчит,
    # явное direct-правило обязательно (см. TELEMOST_DOMAINS)
    assert "domain:telemost.com" in flat
    assert "domain:ozon.com" in flat
    # MAX + its echo-check domains ride the RU leg alongside VK
    assert "domain:max.ru" in flat and "domain:okcdn.ru" in flat and "domain:apptracer.ru" in flat
    assert "domain:api.ipify.org" in flat and "domain:ip.mail.ru" in flat
    # .ru regexp + tld catch-all
    assert any("regexp:" in d for d in flat)
    assert "domain:ru" in flat and "domain:su" in flat and "domain:xn--p1ai" in flat


def test_extra_direct_domains_override_lands_before_geoip_catchall():
    rules = build_canonical_rules(egress_tag="to-exit", has_api=False, extra_direct_domains=YT_DIRECT_DOMAINS)
    # the extra rule sits immediately before geoip:ru so it wins the match
    assert rules[-2] == {"type": "field", "domain": list(YT_DIRECT_DOMAINS), "outboundTag": "direct"}
    assert rules[-1] == {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"}


def test_rebuild_passthrough_extra_direct_domains():
    out = rebuild_config_routing(_cfg("to-exit", has_api=True), extra_direct_domains=YT_DIRECT_DOMAINS)
    yt = next(r for r in out["routing"]["rules"] if "domain:googlevideo.com" in (r.get("domain") or []))
    assert yt["outboundTag"] == "direct"
    # without the knob the YouTube override must NOT appear anywhere
    plain = rebuild_config_routing(_cfg("to-exit", has_api=True))
    assert not any("domain:googlevideo.com" in (r.get("domain") or []) for r in plain["routing"]["rules"])


def test_extra_egress_rules_land_after_foreign_families():
    """cloud.ru TG: domain + ip rules ride the egress leg, placed right after FOREIGN families."""
    rules = build_canonical_rules(
        egress_tag="to-exit",
        has_api=False,
        extra_egress_domains=TG_EGRESS_DOMAINS,
        extra_egress_ips=TG_EGRESS_IPS,
    )
    assert rules[5] == {"type": "field", "domain": list(TG_EGRESS_DOMAINS), "outboundTag": "to-exit"}
    assert rules[6] == {"type": "field", "ip": list(TG_EGRESS_IPS), "outboundTag": "to-exit"}
    # and the first RU ->direct family still follows them
    assert rules[7]["outboundTag"] == "direct"


def test_rebuild_passthrough_extra_egress():
    out = rebuild_config_routing(
        _cfg("to-exit", has_api=True),
        extra_egress_domains=TG_EGRESS_DOMAINS,
        extra_egress_ips=TG_EGRESS_IPS,
    )
    tg = next(r for r in out["routing"]["rules"] if "domain:telegram.org" in (r.get("domain") or []))
    tg_ip = next(r for r in out["routing"]["rules"] if "149.154.160.0/20" in (r.get("ip") or []))
    assert tg["outboundTag"] == "to-exit" and tg_ip["outboundTag"] == "to-exit"
    # without the knobs Telegram must NOT appear anywhere (not a fleet family)
    plain = rebuild_config_routing(_cfg("to-exit", has_api=True))
    assert not any("domain:telegram.org" in (r.get("domain") or []) for r in plain["routing"]["rules"])


def test_targets_registry_selectel_and_tg_knobs():
    """Registry sanity (no network, no filesystem): CDN-SL present with the Selectel origin;
    cloud.ru boxes FI + CDN-SL carry the TG->egress knobs; names unique."""
    from scripts.canonicalize_routing import DEFAULT_SSH_KEY, TARGETS

    names = [t.name for t in TARGETS]
    assert len(names) == len(set(names))
    assert "CDN-SL" in names and "FI" in names
    sl = next(t for t in TARGETS if t.name == "CDN-SL")
    fi = next(t for t in TARGETS if t.name == "FI")
    assert sl.host == "176.108.240.28"  # Selectel CDN origin (vpn_servers id=35)
    assert sl.key == DEFAULT_SSH_KEY and sl.user == "romtan"
    for t in (fi, sl):
        assert t.extra_egress_domains is TG_EGRESS_DOMAINS
        assert t.extra_egress_ips is TG_EGRESS_IPS


def test_build_is_idempotent_and_pure():
    a = build_canonical_rules(egress_tag="e", has_api=True)
    b = build_canonical_rules(egress_tag="e", has_api=True)
    assert a == b
    # mutating the result must not affect a future build
    a[0]["outboundTag"] = "MUTATED"
    c = build_canonical_rules(egress_tag="e", has_api=True)
    assert c[0]["outboundTag"] == "api"


def test_trailing_rules_are_appended_deep_copied():
    trail = [{"type": "field", "inboundTag": ["in-8443"], "outboundTag": "to-exit"}]
    rules = build_canonical_rules(egress_tag="to-exit", has_api=True, trailing_rules=trail)
    assert rules[-1] == trail[0]
    # deep copy: mutating the returned rule does not affect the caller's source
    rules[-1]["outboundTag"] = "direct"
    assert trail[0]["outboundTag"] == "to-exit"


# --- detect_egress_tag --------------------------------------------------------


def test_detect_egress_returns_first_vless():
    cfg = {
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "relay", "protocol": "vless"},
        ]
    }
    assert detect_egress_tag(cfg) == "relay"


def test_detect_egress_raises_without_vless():
    with pytest.raises(ValueError):
        detect_egress_tag({"outbounds": [{"tag": "direct", "protocol": "freedom"}]})


# --- rebuild_config_routing ---------------------------------------------------


def test_rebuild_does_not_mutate_input():
    cfg = _cfg(
        "to-exit", has_api=True, extra_rules=[{"type": "field", "inboundTag": ["in-443"], "outboundTag": "to-exit"}]
    )
    before = copy.deepcopy(cfg)
    rebuild_config_routing(cfg)
    assert cfg == before  # input untouched


def test_rebuild_sets_domain_strategy_and_canonical_rules():
    cfg = _cfg("to-exit", has_api=True)
    out = rebuild_config_routing(cfg)
    assert out["routing"]["domainStrategy"] == "IPIfNonMatch"
    # egress auto-detected from the vless outbound
    egress_rules = [r for r in out["routing"]["rules"] if r.get("outboundTag") == "to-exit"]
    assert egress_rules, "egress-tagged rules should be present"


def test_rebuild_detects_has_api_from_inbounds():
    with_api = rebuild_config_routing(_cfg("to-exit", has_api=True))
    without_api = rebuild_config_routing(_cfg("to-exit", has_api=False))
    assert any(r.get("inboundTag") == ["api"] for r in with_api["routing"]["rules"])
    assert not any(r.get("inboundTag") == ["api"] for r in without_api["routing"]["rules"])


def test_rebuild_preserves_inbound_tagged_trailing_rules():
    trail = [{"type": "field", "inboundTag": ["in-8443"], "outboundTag": "to-exit"}]
    cfg = _cfg("to-exit", has_api=True, extra_rules=trail)
    out = rebuild_config_routing(cfg)
    assert out["routing"]["rules"][-1]["inboundTag"] == ["in-8443"]


def test_rebuild_raises_without_vless_egress():
    cfg = {
        "inbounds": [{"tag": "api", "protocol": "dokodemo-door"}],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {"rules": []},
    }
    with pytest.raises(ValueError):
        rebuild_config_routing(cfg)


# --- parse_grpc_users ---------------------------------------------------------


def _user(email, uuid, flow=True):
    proto = b"\n\x04vless\x12$" + uuid.encode() + (b"\x1a\x10xtls-rprx-vision" if flow else b"")
    return {"email": email, "account": {"value": base64.b64encode(proto).decode()}}


def test_parse_grpc_users_extracts_uuid_and_flow():
    data = json.dumps(
        {
            "users": [
                _user("a@x", "11111111-2222-3333-4444-555555555555"),
                _user("b@x", "22222222-3333-4444-5555-666666666666", flow=False),
            ]
        }
    )
    clients = parse_grpc_users(data)
    assert len(clients) == 2
    by_email = {c["email"]: c for c in clients}
    assert by_email["a@x"]["id"] == "11111111-2222-3333-4444-555555555555"
    assert by_email["a@x"]["flow"] == "xtls-rprx-vision"
    assert by_email["b@x"]["flow"] == ""


def test_parse_grpc_users_skips_invalid_accounts():
    data = json.dumps(
        {
            "users": [
                {
                    "email": "good@x",
                    "account": {
                        "value": base64.b64encode(b"\n\x04vless\x12$33333333-4444-5555-6666-777777777777").decode()
                    },
                },
                {"email": "bad@x", "account": {"value": "!!!not-base64!!!"}},
                {"email": "noid@x", "account": {"value": base64.b64encode(b"no-uuid-here").decode()}},
            ]
        }
    )
    clients = parse_grpc_users(data)
    assert len(clients) == 1
    assert clients[0]["email"] == "good@x"


def test_parse_grpc_users_empty():
    assert parse_grpc_users("") == []
    assert parse_grpc_users("{}") == []
