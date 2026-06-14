"""Tests for the RU egress relay merge logic (manage_ru_egress.merge_ru_routing).

The merge must be NON-DESTRUCTIVE (preserve existing outbounds/rules) and
IDEMPOTENT (re-merging is a no-op), because it edits the xrayTemplateConfig of
production VPN panels that real users are connected to.
"""

from __future__ import annotations

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
