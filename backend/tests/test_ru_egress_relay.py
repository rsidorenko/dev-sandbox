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






