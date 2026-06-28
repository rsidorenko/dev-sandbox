"""Tests for the RU egress relay merge logic (manage_ru_egress.merge_ru_routing).

The merge must be NON-DESTRUCTIVE (preserve existing outbounds/rules) and
IDEMPOTENT (re-merging is a no-op), because it edits the xrayTemplateConfig of
production VPN panels that real users are connected to.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

# Import the operator script from backend/scripts (it has no heavy deps at import).
_SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, _SCRIPTS)
import manage_ru_egress as mre  # noqa: E402


def _default_template() -> dict:
    """A representative 3x-ui default xrayTemplateConfig (minimal routing)."""
    return {
        "log": {"loglevel": "warning"},
        "api": {"services": ["HandlerService", "LoggerService", "StatsService"], "tag": "api"},
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [{"inboundTag": ["api"], "outboundTag": "api"}],
        },
        "inbounds": [
            {"tag": "api", "listen": "127.0.0.1", "port": 62789, "protocol": "dokodemo-door",
             "settings": {"address": "127.0.0.1"}}
        ],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
    }


def _outbound_tags(t: dict) -> list[str]:
    return [o.get("tag") for o in t.get("outbounds", [])]


def _rule_tags(t: dict) -> list:
    return [(r.get("outboundTag"), tuple(r.get("domain") or ()), tuple(r.get("ip") or ()))
            for r in t.get("routing", {}).get("rules", [])]


def test_merge_is_non_destructive_preserves_existing_outbounds_and_rules() -> None:
    before = _default_template()
    after = mre.merge_ru_routing(before)

    # Existing outbounds preserved + relay added.
    assert "direct" in _outbound_tags(after)
    assert "block" in _outbound_tags(after)
    assert mre.RELAY_OUTBOUND_TAG in _outbound_tags(after)
    # The api rule is preserved.
    assert ("api", (), ()) in _rule_tags(after)
    # The original template is NOT mutated.
    assert before["routing"]["domainStrategy"] == "AsIs"
    assert mre.RELAY_OUTBOUND_TAG not in _outbound_tags(before)


def test_merge_sets_domain_strategy_for_geoip_resolution() -> None:
    after = mre.merge_ru_routing(_default_template())
    assert after["routing"]["domainStrategy"] == "IPIfNonMatch"


def test_merge_adds_ru_rules_pointing_to_relay() -> None:
    after = mre.merge_ru_routing(_default_template())
    tags = _rule_tags(after)
    assert (mre.RELAY_OUTBOUND_TAG, tuple(mre.RU_DOMAINS), ()) in tags
    assert (mre.RELAY_OUTBOUND_TAG, (), ("geoip:ru",)) in tags


def test_merge_inserts_ru_rules_after_api_and_private() -> None:
    template = _default_template()
    # Add a geoip:private -> direct rule and a catch-all after it.
    template["routing"]["rules"] = [
        {"inboundTag": ["api"], "outboundTag": "api"},
        {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"},
        {"type": "field", "network": "tcp,udp", "outboundTag": "direct"},
    ]
    after = mre.merge_ru_routing(template)
    rules = after["routing"]["rules"]
    tags = [r.get("outboundTag") for r in rules]
    # RU rules come AFTER api and AFTER geoip:private, but BEFORE the catch-all.
    assert tags.index("api") < tags.index(mre.RELAY_OUTBOUND_TAG)
    priv_idx = next(i for i, r in enumerate(rules)
                    if any("geoip:private" in str(x) for x in (r.get("ip") or [])))
    relay_idx = tags.index(mre.RELAY_OUTBOUND_TAG)
    catchall_idx = next(i for i, r in enumerate(rules) if r.get("network") == "tcp,udp")
    assert priv_idx < relay_idx < catchall_idx


def test_merge_is_idempotent() -> None:
    once = mre.merge_ru_routing(_default_template())
    twice = mre.merge_ru_routing(once)
    # Same outbound tags, same rules, same domainStrategy.
    assert _outbound_tags(once) == _outbound_tags(twice)
    assert _rule_tags(once) == _rule_tags(twice)
    assert once["routing"]["domainStrategy"] == twice["routing"]["domainStrategy"]
    # Exactly one relay outbound and exactly two RU rules.
    assert _outbound_tags(twice).count(mre.RELAY_OUTBOUND_TAG) == 1
    relay_rules = [r for r in twice["routing"]["rules"]
                   if r.get("outboundTag") == mre.RELAY_OUTBOUND_TAG]
    assert len(relay_rules) == 2


def test_relay_outbound_targets_ru_server_with_reality_params() -> None:
    after = mre.merge_ru_routing(_default_template())
    relay = next(o for o in after["outbounds"] if o.get("tag") == mre.RELAY_OUTBOUND_TAG)
    vnext = relay["settings"]["vnext"][0]
    assert vnext["address"] == mre.RU_RELAY_HOST
    assert vnext["port"] == 443
    assert vnext["users"][0]["id"] == mre.RELAY_UUID
    rs = relay["streamSettings"]["realitySettings"]
    assert rs["publicKey"] == mre.RU_RELAY_PBK
    assert rs["shortId"] == mre.RU_RELAY_SID
    assert rs["serverName"] == mre.RU_RELAY_SNI
    assert relay["streamSettings"]["network"] == "tcp"
    assert relay["streamSettings"]["security"] == "reality"


def test_merge_preserves_custom_outbounds_and_rules() -> None:
    template = _default_template()
    template["outbounds"].append({"tag": "my-custom", "protocol": "freedom"})
    template["routing"]["rules"].append(
        {"type": "field", "domain": ["ext:example.com"], "outboundTag": "my-custom"}
    )
    after = mre.merge_ru_routing(template)
    assert "my-custom" in _outbound_tags(after)
    assert ("my-custom", ("ext:example.com",), ()) in _rule_tags(after)


def test_merge_handles_template_without_routing_or_outbounds() -> None:
    template = {"log": {"loglevel": "warning"}}
    after = mre.merge_ru_routing(template)
    assert after["routing"]["domainStrategy"] == "IPIfNonMatch"
    assert mre.RELAY_OUTBOUND_TAG in _outbound_tags(after)
    assert len(after["routing"]["rules"]) >= 2


# ── export/import path (make a panel match another's working ru-relay) ───────


def _sample_export() -> dict:
    """Mimics what `export` would emit from Frankfurt's working ru-relay config."""
    return {
        "outbound": {
            "tag": "ru-relay",
            "protocol": "vless",
            "settings": {"vnext": [{"address": "89.169.139.153", "port": 443,
                                    "users": [{"id": "RELAY-UUID-FROM-FRANKFURT",
                                               "encryption": "none", "flow": ""}]}]},
            "streamSettings": {"network": "tcp", "security": "reality",
                               "realitySettings": {"serverName": "max.ru", "publicKey": "PBK",
                                                   "shortId": "a1b2c3d4e5f6",
                                                   "fingerprint": "chrome"}},
        },
        "rules": [
            {"type": "field",
             "domain": ["geosite:category-ru", "geosite:tld-ru", "domain-suffix:ru",
                        "domain-suffix:su", "domain-suffix:xn--p1ai"],
             "outboundTag": "ru-relay"},
            {"type": "field", "ip": ["geoip:ru"], "outboundTag": "ru-relay"},
        ],
    }


def test_apply_exported_routing_is_non_destructive_and_preserves_existing() -> None:
    export = _sample_export()
    after = mre.apply_exported_routing(_default_template(), export["outbound"], export["rules"])
    # Existing outbounds preserved + ru-relay added.
    assert "direct" in _outbound_tags(after)
    assert "block" in _outbound_tags(after)
    assert "ru-relay" in _outbound_tags(after)
    # api rule preserved.
    assert ("api", (), ()) in _rule_tags(after)
    # Input not mutated.
    assert "ru-relay" not in _outbound_tags(_default_template())


def test_apply_exported_routing_uses_exported_uuid_and_target_verbatim() -> None:
    export = _sample_export()
    after = mre.apply_exported_routing(_default_template(), export["outbound"], export["rules"])
    relay = next(o for o in after["outbounds"] if o.get("tag") == "ru-relay")
    vnext = relay["settings"]["vnext"][0]
    assert vnext["address"] == "89.169.139.153"
    assert vnext["users"][0]["id"] == "RELAY-UUID-FROM-FRANKFURT"
    assert relay["streamSettings"]["realitySettings"]["serverName"] == "max.ru"
    # The exported rules are present with their geosite/domain-suffix domains.
    tags = _rule_tags(after)
    assert ("ru-relay", ("geosite:category-ru", "geosite:tld-ru", "domain-suffix:ru",
                         "domain-suffix:su", "domain-suffix:xn--p1ai"), ()) in tags
    assert ("ru-relay", (), ("geoip:ru",)) in tags


def test_apply_exported_routing_inserts_after_api_and_private() -> None:
    export = _sample_export()
    template = _default_template()
    template["routing"]["rules"] = [
        {"inboundTag": ["api"], "outboundTag": "api"},
        {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
        {"type": "field", "network": "tcp,udp", "outboundTag": "direct"},
    ]
    after = mre.apply_exported_routing(template, export["outbound"], export["rules"])
    tags = [r.get("outboundTag") for r in after["routing"]["rules"]]
    priv_idx = next(i for i, r in enumerate(after["routing"]["rules"])
                    if any("geoip:private" in str(x) for x in (r.get("ip") or [])))
    relay_idx = tags.index("ru-relay")
    catchall_idx = next(i for i, r in enumerate(after["routing"]["rules"])
                        if r.get("network") == "tcp,udp")
    assert after["routing"]["rules"][0].get("outboundTag") == "api"
    assert priv_idx < relay_idx < catchall_idx


def test_apply_exported_routing_is_idempotent() -> None:
    export = _sample_export()
    once = mre.apply_exported_routing(_default_template(), export["outbound"], export["rules"])
    twice = mre.apply_exported_routing(once, export["outbound"], export["rules"])
    assert _outbound_tags(once) == _outbound_tags(twice)
    assert _rule_tags(once) == _rule_tags(twice)
    assert _outbound_tags(twice).count("ru-relay") == 1
    assert sum(1 for r in twice["routing"]["rules"] if r.get("outboundTag") == "ru-relay") == 2


# ── repoint (re-target an existing ru-relay outbound to 89.169.139.153) ──────


def _panel_with_wrong_target_relay() -> dict:
    """A panel whose ru-relay outbound targets the WRONG relay (51.250.102.219),
    as found on Frankfurt/LA live. repoint should fix only the target."""
    return {
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": [
            {"inboundTag": ["api"], "outboundTag": "api"},
            {"ip": ["geoip:private"], "outboundTag": "blocked"},
            {"domain": ["geosite:category-ru"], "outboundTag": "ru-relay"},
            {"ip": ["geoip:ru"], "outboundTag": "ru-relay"},
        ]},
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "blocked", "protocol": "blackhole"},
            {"tag": "ru-relay", "protocol": "vless",
             "settings": {"vnext": [{"address": "51.250.102.219", "port": 443,
                                     "users": [{"id": "0b99b2fb-wrong", "encryption": "none",
                                                "flow": ""}]}]},
             "streamSettings": {"network": "tcp", "security": "reality",
                                "realitySettings": {"serverName": "ya.ru",
                                                    "publicKey": "WRONG-PBK",
                                                    "shortId": "a1b2c3", "fingerprint": "chrome"}}},
        ],
    }


def test_repoint_changes_only_target_keeps_tag_and_rules() -> None:
    before = _panel_with_wrong_target_relay()
    after = mre.repoint_ru_relay_outbound(before, "ru-relay")

    # Target + UUID + Reality params now the canonical 89.169.139.153 ones.
    relay = next(o for o in after["outbounds"] if o.get("tag") == "ru-relay")
    vnext = relay["settings"]["vnext"][0]
    assert vnext["address"] == mre.RU_RELAY_HOST        # 89.169.139.153
    assert vnext["port"] == 443
    assert vnext["users"][0]["id"] == mre.RELAY_UUID     # 00607f0b-…
    rs = relay["streamSettings"]["realitySettings"]
    assert rs["publicKey"] == mre.RU_RELAY_PBK
    assert rs["shortId"] == mre.RU_RELAY_SID
    assert rs["serverName"] == mre.RU_RELAY_SNI          # max.ru

    # Tag preserved, all routing rules preserved (incl. the ru-relay rules).
    assert _outbound_tags(after) == ["direct", "blocked", "ru-relay"]
    assert _rule_tags(after) == _rule_tags(before)
    # No new outbound or rule was added.
    assert len(after["outbounds"]) == len(before["outbounds"])
    assert len(after["routing"]["rules"]) == len(before["routing"]["rules"])
    # Input not mutated.
    wrong = next(o for o in before["outbounds"] if o.get("tag") == "ru-relay")
    assert wrong["settings"]["vnext"][0]["address"] == "51.250.102.219"


def test_repoint_is_idempotent() -> None:
    once = mre.repoint_ru_relay_outbound(_panel_with_wrong_target_relay(), "ru-relay")
    twice = mre.repoint_ru_relay_outbound(once, "ru-relay")
    relay = next(o for o in twice["outbounds"] if o.get("tag") == "ru-relay")
    assert relay["settings"]["vnext"][0]["address"] == mre.RU_RELAY_HOST
    assert relay["settings"]["vnext"][0]["users"][0]["id"] == mre.RELAY_UUID


def test_repoint_raises_when_no_such_outbound() -> None:
    with pytest.raises(ValueError):
        mre.repoint_ru_relay_outbound(_default_template(), "ru-relay")


# ── purge-orphans: user-client detection ────────────────────────────────────


@pytest.mark.parametrize("email,expected", [
    ("user-u8158783115", True),
    ("cdn-user-abc123def456", True),
    ("x-user-something", True),
    ("relay-from-foreign", False),   # the shared relay UUID — MUST be kept
    ("relay-from-lte", False),
    ("", False),
    ("admin", False),
    ("some-other-email", False),
])
def test_is_user_client(email: str, expected: bool) -> None:
    assert mre._is_user_client(email) is expected


def test_is_user_client_handles_none() -> None:
    assert mre._is_user_client(None) is False  # type: ignore[arg-type]


# ── fix-sniffing: standardize inbound sniffing (fixes 2.0 ws RU routing) ─────


def test_standardize_sniffing_flags_the_broken_ws_config() -> None:
    # The 2.0 ws inbound live config that broke RU routing.
    broken = '{"enabled": true, "destOverride": ["http", "tls"], "routeOnly": true}'
    out = mre.standardize_sniffing(broken)
    assert out is not None
    parsed = json.loads(out)
    assert parsed["routeOnly"] is False
    assert "quic" in parsed["destOverride"]


def test_standardize_sniffing_leaves_working_config_unchanged() -> None:
    # The 1.0/3.0 tcp/xhttp config (already standard).
    ok = '{"enabled": true, "destOverride": ["http", "tls", "quic"], "routeOnly": false}'
    assert mre.standardize_sniffing(ok) is None


def test_standardize_sniffing_tolerates_malformed() -> None:
    assert mre.standardize_sniffing("") is not None
    assert mre.standardize_sniffing("not-json") is not None
    assert mre.standardize_sniffing(None) is not None  # type: ignore[arg-type]


# ── reset-logs: ensure diagnostic log section ────────────────────────────────


def test_ensure_log_section_sets_diagnostic_log_and_preserves_rest() -> None:
    template = {
        "log": {"loglevel": "warning"},
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": [{"outboundTag": "api"}]},
        "outbounds": [{"tag": "direct"}],
    }
    out = mre.ensure_log_section(template)
    assert out["log"]["loglevel"] == "info"
    assert out["log"]["access"] == "/var/log/xray-access.log"
    assert out["log"]["error"] == "/var/log/xray-error.log"
    # rest preserved
    assert out["routing"]["domainStrategy"] == "IPIfNonMatch"
    assert out["outbounds"] == [{"tag": "direct"}]
    # input not mutated
    assert template["log"]["loglevel"] == "warning"


def test_ensure_log_section_adds_log_when_absent() -> None:
    out = mre.ensure_log_section({"routing": {}})
    assert out["log"]["loglevel"] == "info"
    assert out["routing"] == {}


# ── clean-relay: convert the RU relay to a pure RU-egress ────────────────────


def _relay_with_helsinki_catchall() -> dict:
    """The RU relay's live config: RU rules -> direct, but a vestigial catch-all
    forwards the rest to Helsinki (which rejects it -> sites break)."""
    return {
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": [
            {"inboundTag": ["api"], "outboundTag": "api"},
            {"ip": ["geoip:private"], "outboundTag": "direct"},
            {"inboundTag": ["in-443-tcp"], "domain": ["domain:ru", "domain:su"], "outboundTag": "direct"},
            {"inboundTag": ["in-443-tcp"], "ip": ["geoip:ru"], "outboundTag": "direct"},
            {"inboundTag": ["in-443-tcp"], "network": "tcp,udp", "outboundTag": "relay-to-helsinki"},
        ]},
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "relay-to-helsinki", "protocol": "vless",
             "settings": {"vnext": [{"address": "77.221.159.106", "port": 443}]}},
            {"tag": "blocked", "protocol": "blackhole"},
        ],
    }


def test_clean_relay_redirects_catchall_to_direct_and_drops_outbound() -> None:
    after = mre.clean_relay_routing(_relay_with_helsinki_catchall())
    tags = [o.get("tag") for o in after["outbounds"]]
    assert "relay-to-helsinki" not in tags
    assert "direct" in tags and "blocked" in tags   # other outbounds preserved
    # The catch-all now egresses directly from RU instead of forwarding to Helsinki.
    catchall = next(r for r in after["routing"]["rules"] if r.get("network") == "tcp,udp")
    assert catchall["outboundTag"] == "direct"


def test_clean_relay_preserves_ru_direct_and_api_rules() -> None:
    after = mre.clean_relay_routing(_relay_with_helsinki_catchall())
    ob_tags = [r.get("outboundTag") for r in after["routing"]["rules"]]
    assert "api" in ob_tags                      # api rule preserved
    ru_rule = next(r for r in after["routing"]["rules"] if "domain:ru" in (r.get("domain") or []))
    assert ru_rule["outboundTag"] == "direct"    # RU-domain rule untouched


def test_clean_relay_is_idempotent() -> None:
    once = mre.clean_relay_routing(_relay_with_helsinki_catchall())
    twice = mre.clean_relay_routing(once)
    assert once == twice
    assert "relay-to-helsinki" not in [o.get("tag") for o in twice["outbounds"]]


def test_clean_relay_noop_when_already_pure_egress() -> None:
    pure = {
        "routing": {"rules": [{"network": "tcp,udp", "outboundTag": "direct"}]},
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }
    assert mre.clean_relay_routing(pure) == pure


def test_clean_relay_does_not_mutate_input() -> None:
    before = _relay_with_helsinki_catchall()
    mre.clean_relay_routing(before)
    assert "relay-to-helsinki" in [o.get("tag") for o in before["outbounds"]]   # input unchanged


# ── apply-vk: explicit VK-domains -> ru-relay routing ────────────────────────


def _panel_with_ru_relay(tag: str = "ru-relay") -> dict:
    """A foreign panel that already has the RU relay configured (outbound + RU
    rules), as found live on Helsinki/Frankfurt/Lithuania/Albania (tag ru-relay,
    IPIfNonMatch) and LA (tag ru-relay, AsIs, no geoip:ru). merge_vk_routing adds
    the explicit VK rule on top of this."""
    return {
        "log": {"loglevel": "warning"},
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": [
            {"inboundTag": ["api"], "outboundTag": "api"},
            {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
            {"type": "field", "domain": ["geosite:CATEGORY-RU", "geosite:TLD-RU",
                                         "domain-suffix:ru", "domain-suffix:su",
                                         "domain-suffix:xn--p1ai"], "outboundTag": tag},
            {"type": "field", "ip": ["geoip:ru"], "outboundTag": tag},
            {"type": "field", "network": "tcp,udp", "outboundTag": "direct"},
        ]},
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "blocked", "protocol": "blackhole"},
            {"tag": tag, "protocol": "vless",
             "settings": {"vnext": [{"address": mre.RU_RELAY_HOST, "port": 443,
                                     "users": [{"id": mre.RELAY_UUID, "encryption": "none",
                                                "flow": ""}]}]}},
        ],
    }


def test_merge_vk_routing_adds_vk_rule_pointing_to_relay() -> None:
    after = mre.merge_vk_routing(_panel_with_ru_relay())
    assert ("ru-relay", tuple(mre.VK_DOMAINS), ()) in _rule_tags(after)


def test_merge_vk_routing_auto_detects_tag_both_styles() -> None:
    # The canonical ru-relay tag (export/import path) AND the built-in
    # relay-to-russia tag (apply path) must both be detected via the outbound's
    # vnext address.
    for tag in ("ru-relay", "relay-to-russia"):
        after = mre.merge_vk_routing(_panel_with_ru_relay(tag))
        assert (tag, tuple(mre.VK_DOMAINS), ()) in _rule_tags(after)


def test_merge_vk_routing_uses_explicit_tag_when_given() -> None:
    # An explicit tag is honored even when no relay outbound is detectable
    # (cmd_apply_vk guards detection; the pure function trusts the caller's tag).
    after = mre.merge_vk_routing(_default_template(), relay_tag="ru-relay")
    assert ("ru-relay", tuple(mre.VK_DOMAINS), ()) in _rule_tags(after)


def test_merge_vk_routing_inserts_after_ru_rules_before_catchall() -> None:
    after = mre.merge_vk_routing(_panel_with_ru_relay())
    rules = after["routing"]["rules"]
    vk_idx = next(i for i, r in enumerate(rules)
                  if set(r.get("domain") or []) == set(mre.VK_DOMAINS))
    catchall_idx = next(i for i, r in enumerate(rules) if r.get("network") == "tcp,udp")
    # VK rule comes AFTER the ru-relay rules and BEFORE the catch-all.
    assert rules.index(next(r for r in rules if r.get("outboundTag") == "ru-relay")) < vk_idx
    assert vk_idx < catchall_idx


def test_merge_vk_routing_preserves_existing_rules_and_outbounds() -> None:
    before = _panel_with_ru_relay()
    after = mre.merge_vk_routing(before)
    # All original outbounds still present.
    assert set(_outbound_tags(before)).issubset(set(_outbound_tags(after)))
    # The existing RU rules are kept.
    assert ("ru-relay", (), ("geoip:ru",)) in _rule_tags(after)
    assert ("ru-relay", ("geosite:CATEGORY-RU", "geosite:TLD-RU", "domain-suffix:ru",
                         "domain-suffix:su", "domain-suffix:xn--p1ai"), ()) in _rule_tags(after)
    # Exactly one VK rule added.
    vk = [r for r in after["routing"]["rules"] if set(r.get("domain") or []) == set(mre.VK_DOMAINS)]
    assert len(vk) == 1


def test_merge_vk_routing_is_idempotent() -> None:
    once = mre.merge_vk_routing(_panel_with_ru_relay())
    twice = mre.merge_vk_routing(once)
    assert _outbound_tags(once) == _outbound_tags(twice)
    assert _rule_tags(once) == _rule_tags(twice)
    vk = [r for r in twice["routing"]["rules"] if set(r.get("domain") or []) == set(mre.VK_DOMAINS)]
    assert len(vk) == 1


def test_merge_vk_routing_raises_when_no_relay_outbound() -> None:
    # _default_template has no vless/relay outbound -> detection fails -> refuse.
    with pytest.raises(ValueError):
        mre.merge_vk_routing(_default_template())


def test_merge_vk_routing_does_not_mutate_input() -> None:
    before = _panel_with_ru_relay()
    before_rules = _rule_tags(before)
    mre.merge_vk_routing(before)
    assert _rule_tags(before) == before_rules
    assert not any(set(r.get("domain") or []) == set(mre.VK_DOMAINS)
                   for r in before["routing"]["rules"])


def test_detect_relay_tag_finds_by_address_then_rule_fallback() -> None:
    # By outbound vnext address.
    assert mre._detect_relay_tag(_panel_with_ru_relay("ru-relay")) == "ru-relay"
    # Fallback: tag inferred from a domain:ru rule when no matching vnext address.
    fallback = {"outbounds": [{"tag": "direct", "protocol": "freedom"}],
                "routing": {"rules": [{"domain": ["domain:ru"], "outboundTag": "my-relay"}]}}
    assert mre._detect_relay_tag(fallback) == "my-relay"
    # None when neither signal is present.
    assert mre._detect_relay_tag(_default_template()) is None


def test_has_vk_rule_detects_exact_domain_set() -> None:
    tag = "ru-relay"
    assert mre._has_vk_rule([{"outboundTag": tag, "domain": list(mre.VK_DOMAINS)}], tag) is True
    # A partial / different domain set is NOT the VK rule.
    assert mre._has_vk_rule([{"outboundTag": tag, "domain": ["domain:vk.com"]}], tag) is False
    assert mre._has_vk_rule([], tag) is False


# ── apply-max: explicit MAX-domains (max.ru + oneme.ru) -> ru-relay routing ───
# MAX (max.ru) is a VK-group super-app with anti-fraud + a VPN-detection module,
# so its auth/API must egress from the RU relay — same rationale as VK. merge_max_routing
# is a thin wrapper over the same generic merge_domain_family_routing as merge_vk_routing.


def test_merge_max_routing_adds_max_rule_pointing_to_relay() -> None:
    after = mre.merge_max_routing(_panel_with_ru_relay())
    assert ("ru-relay", tuple(mre.MAX_DOMAINS), ()) in _rule_tags(after)


def test_merge_max_routing_auto_detects_tag_both_styles() -> None:
    # Both the canonical ru-relay tag and the built-in relay-to-russia tag are
    # detected via the outbound's vnext address.
    for tag in ("ru-relay", "relay-to-russia"):
        after = mre.merge_max_routing(_panel_with_ru_relay(tag))
        assert (tag, tuple(mre.MAX_DOMAINS), ()) in _rule_tags(after)


def test_merge_max_routing_uses_explicit_tag_when_given() -> None:
    after = mre.merge_max_routing(_default_template(), relay_tag="ru-relay")
    assert ("ru-relay", tuple(mre.MAX_DOMAINS), ()) in _rule_tags(after)


def test_merge_max_routing_inserts_after_ru_rules_before_catchall() -> None:
    after = mre.merge_max_routing(_panel_with_ru_relay())
    rules = after["routing"]["rules"]
    max_idx = next(i for i, r in enumerate(rules)
                   if set(r.get("domain") or []) == set(mre.MAX_DOMAINS))
    catchall_idx = next(i for i, r in enumerate(rules) if r.get("network") == "tcp,udp")
    # MAX rule comes AFTER the ru-relay rules and BEFORE the catch-all.
    assert rules.index(next(r for r in rules if r.get("outboundTag") == "ru-relay")) < max_idx
    assert max_idx < catchall_idx


def test_merge_max_routing_preserves_existing_rules_and_outbounds() -> None:
    before = _panel_with_ru_relay()
    after = mre.merge_max_routing(before)
    assert set(_outbound_tags(before)).issubset(set(_outbound_tags(after)))
    assert ("ru-relay", (), ("geoip:ru",)) in _rule_tags(after)
    # Exactly one MAX rule added.
    mx = [r for r in after["routing"]["rules"] if set(r.get("domain") or []) == set(mre.MAX_DOMAINS)]
    assert len(mx) == 1


def test_merge_max_routing_is_idempotent() -> None:
    once = mre.merge_max_routing(_panel_with_ru_relay())
    twice = mre.merge_max_routing(once)
    assert _outbound_tags(once) == _outbound_tags(twice)
    assert _rule_tags(once) == _rule_tags(twice)
    mx = [r for r in twice["routing"]["rules"] if set(r.get("domain") or []) == set(mre.MAX_DOMAINS)]
    assert len(mx) == 1


def test_merge_max_routing_raises_when_no_relay_outbound() -> None:
    with pytest.raises(ValueError):
        mre.merge_max_routing(_default_template())


def test_merge_max_routing_does_not_mutate_input() -> None:
    before = _panel_with_ru_relay()
    before_rules = _rule_tags(before)
    mre.merge_max_routing(before)
    assert _rule_tags(before) == before_rules
    assert not any(set(r.get("domain") or []) == set(mre.MAX_DOMAINS)
                   for r in before["routing"]["rules"])


def test_has_max_rule_detects_exact_domain_set() -> None:
    tag = "ru-relay"
    assert mre._has_max_rule([{"outboundTag": tag, "domain": list(mre.MAX_DOMAINS)}], tag) is True
    assert mre._has_max_rule([{"outboundTag": tag, "domain": ["domain:max.ru"]}], tag) is False
    assert mre._has_max_rule([], tag) is False


def test_has_domain_rule_is_the_generic_backend_for_vk_and_max() -> None:
    """_has_vk_rule / _has_max_rule both delegate to _has_domain_rule, so the exact
    domain-set match is consistent across families."""
    tag = "ru-relay"
    assert mre._has_domain_rule([{"outboundTag": tag, "domain": list(mre.VK_DOMAINS)}], tag, mre.VK_DOMAINS)
    assert mre._has_domain_rule([{"outboundTag": tag, "domain": list(mre.MAX_DOMAINS)}], tag, mre.MAX_DOMAINS)
    # MAX rule is NOT a VK rule and vice versa.
    assert not mre._has_domain_rule([{"outboundTag": tag, "domain": list(mre.VK_DOMAINS)}], tag, mre.MAX_DOMAINS)


def test_merge_vk_and_max_rules_coexist() -> None:
    """A panel can carry BOTH the VK and MAX explicit rules: both present, both after
    the ru-relay rules, both before the catch-all. (VK + MAX domains are disjoint, so
    rule order between them is irrelevant.)"""
    panel = _panel_with_ru_relay()
    after = mre.merge_max_routing(mre.merge_vk_routing(panel))
    rules = after["routing"]["rules"]
    vk_idx = next(i for i, r in enumerate(rules) if set(r.get("domain") or []) == set(mre.VK_DOMAINS))
    max_idx = next(i for i, r in enumerate(rules) if set(r.get("domain") or []) == set(mre.MAX_DOMAINS))
    catchall_idx = next(i for i, r in enumerate(rules) if r.get("network") == "tcp,udp")
    assert vk_idx != max_idx
    assert vk_idx < catchall_idx and max_idx < catchall_idx
    # Exactly one of each.
    assert len([r for r in rules if set(r.get("domain") or []) == set(mre.VK_DOMAINS)]) == 1
    assert len([r for r in rules if set(r.get("domain") or []) == set(mre.MAX_DOMAINS)]) == 1


def test_merge_max_routing_via_generic_helper_matches_wrapper() -> None:
    """merge_max_routing is a thin wrapper over merge_domain_family_routing — they
    produce identical output (guards the refactor that extracted the generic helper)."""
    panel = _panel_with_ru_relay()
    via_wrapper = mre.merge_max_routing(panel)
    via_generic = mre.merge_domain_family_routing(panel, mre.MAX_DOMAINS)
    assert _rule_tags(via_wrapper) == _rule_tags(via_generic)
    assert _outbound_tags(via_wrapper) == _outbound_tags(via_generic)


# ── apply-tiktok: explicit TIKTOK_DOMAINS -> direct routing (INVERSE of VK/MAX) ────
# TikTok must egress from the FOREIGN server (direct), NEVER the RU relay — even if a
# TikTok host resolves to a RU IP (geoip:ru) or is a .ru domain (domain-suffix:ru, e.g.
# tiktok.ru). So the rule is inserted BEFORE the ru-relay rules (first-match wins) — the
# OPPOSITE placement of VK/MAX. TikTok targets the `direct` freedom outbound, so it does
# NOT require a relay outbound to exist.


def test_merge_tiktok_direct_adds_tiktok_rule_pointing_to_direct() -> None:
    after = mre.merge_tiktok_direct(_panel_with_ru_relay())
    assert ("direct", tuple(mre.TIKTOK_DOMAINS), ()) in _rule_tags(after)


def test_merge_tiktok_direct_inserted_before_relay_rules() -> None:
    """THE key inversion vs VK/MAX: the TikTok rule must come BEFORE the ru-relay rules
    (domain-suffix:ru + geoip:ru), so a tiktok.ru / RU-resolving TikTok host matches
    TikTok -> direct first instead of falling to the relay."""
    after = mre.merge_tiktok_direct(_panel_with_ru_relay())
    rules = after["routing"]["rules"]
    tt_idx = next(i for i, r in enumerate(rules)
                  if set(r.get("domain") or []) == set(mre.TIKTOK_DOMAINS))
    first_relay_idx = next(i for i, r in enumerate(rules) if r.get("outboundTag") == "ru-relay")
    assert tt_idx < first_relay_idx
    # Specifically before the domain-suffix:ru rule and the geoip:ru rule.
    dsru_idx = next(i for i, r in enumerate(rules)
                    if "domain-suffix:ru" in (r.get("domain") or []))
    geoipru_idx = next(i for i, r in enumerate(rules)
                       if "geoip:ru" in (r.get("ip") or []))
    assert tt_idx < dsru_idx and tt_idx < geoipru_idx


def test_merge_tiktok_direct_auto_detects_freedom_tag() -> None:
    # The direct tag is taken from the freedom outbound, even when it isn't literally
    # "direct".
    panel = _panel_with_ru_relay()
    for o in panel["outbounds"]:
        if o.get("protocol") == "freedom":
            o["tag"] = "my-direct"
    after = mre.merge_tiktok_direct(panel)
    assert ("my-direct", tuple(mre.TIKTOK_DOMAINS), ()) in _rule_tags(after)


def test_merge_tiktok_direct_uses_explicit_tag_when_given() -> None:
    after = mre.merge_tiktok_direct(_default_template(), direct_tag="direct")
    assert ("direct", tuple(mre.TIKTOK_DOMAINS), ()) in _rule_tags(after)


def test_merge_tiktok_direct_fallback_when_no_relay_rule() -> None:
    """With no relay rules to beat, the rule still inserts (after api / geoip:private)
    pointing at direct — TikTok-direct does not require a relay outbound."""
    after = mre.merge_tiktok_direct(_default_template())
    rules = after["routing"]["rules"]
    assert ("direct", tuple(mre.TIKTOK_DOMAINS), ()) in _rule_tags(after)
    tt = [r for r in rules if set(r.get("domain") or []) == set(mre.TIKTOK_DOMAINS)]
    assert len(tt) == 1


def test_merge_tiktok_direct_preserves_existing_rules_and_outbounds() -> None:
    before = _panel_with_ru_relay()
    after = mre.merge_tiktok_direct(before)
    assert set(_outbound_tags(before)).issubset(set(_outbound_tags(after)))
    assert ("ru-relay", (), ("geoip:ru",)) in _rule_tags(after)
    tt = [r for r in after["routing"]["rules"] if set(r.get("domain") or []) == set(mre.TIKTOK_DOMAINS)]
    assert len(tt) == 1


def test_merge_tiktok_direct_is_idempotent() -> None:
    once = mre.merge_tiktok_direct(_panel_with_ru_relay())
    twice = mre.merge_tiktok_direct(once)
    assert _outbound_tags(once) == _outbound_tags(twice)
    assert _rule_tags(once) == _rule_tags(twice)
    tt = [r for r in twice["routing"]["rules"] if set(r.get("domain") or []) == set(mre.TIKTOK_DOMAINS)]
    assert len(tt) == 1


def test_merge_tiktok_direct_raises_when_no_direct_outbound() -> None:
    # A panel with no freedom/direct outbound -> refuse (would point at a dead outbound).
    no_direct = {"outbounds": [{"tag": "blocked", "protocol": "blackhole"},
                               {"tag": "ru-relay", "protocol": "vless",
                                "settings": {"vnext": [{"address": mre.RU_RELAY_HOST}]}}],
                 "routing": {"rules": []}}
    with pytest.raises(ValueError):
        mre.merge_tiktok_direct(no_direct)


def test_merge_tiktok_direct_does_not_mutate_input() -> None:
    before = _panel_with_ru_relay()
    before_rules = _rule_tags(before)
    mre.merge_tiktok_direct(before)
    assert _rule_tags(before) == before_rules
    assert not any(set(r.get("domain") or []) == set(mre.TIKTOK_DOMAINS)
                   for r in before["routing"]["rules"])


def test_has_tiktok_rule_detects_exact_domain_set() -> None:
    tag = "direct"
    assert mre._has_tiktok_rule([{"outboundTag": tag, "domain": list(mre.TIKTOK_DOMAINS)}], tag) is True
    assert mre._has_tiktok_rule([{"outboundTag": tag, "domain": ["domain:tiktok.com"]}], tag) is False
    assert mre._has_tiktok_rule([], tag) is False


def test_merge_tiktok_coexists_with_vk_and_max() -> None:
    """All three explicit families on one panel: TikTok -> direct (BEFORE the relay
    rules), VK + MAX -> relay (AFTER). Disjoint domains, so order among the relay
    families is irrelevant; what matters is TikTok precedes the relay rules and VK/MAX
    follow them."""
    panel = _panel_with_ru_relay()
    after = mre.merge_tiktok_direct(mre.merge_max_routing(mre.merge_vk_routing(panel)))
    rules = after["routing"]["rules"]
    tt_idx = next(i for i, r in enumerate(rules) if set(r.get("domain") or []) == set(mre.TIKTOK_DOMAINS))
    first_relay_idx = next(i for i, r in enumerate(rules) if r.get("outboundTag") == "ru-relay")
    vk_idx = next(i for i, r in enumerate(rules) if set(r.get("domain") or []) == set(mre.VK_DOMAINS))
    max_idx = next(i for i, r in enumerate(rules) if set(r.get("domain") or []) == set(mre.MAX_DOMAINS))
    # TikTok is the first explicit-family rule (before the relay rules); VK/MAX follow.
    assert tt_idx < first_relay_idx
    assert first_relay_idx < vk_idx and first_relay_idx < max_idx
    assert len([r for r in rules if set(r.get("domain") or []) == set(mre.TIKTOK_DOMAINS)]) == 1
    assert len([r for r in rules if set(r.get("domain") or []) == set(mre.VK_DOMAINS)]) == 1
    assert len([r for r in rules if set(r.get("domain") or []) == set(mre.MAX_DOMAINS)]) == 1







