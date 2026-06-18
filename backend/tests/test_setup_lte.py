"""Tests for the LTE entry xrayTemplateConfig (setup_lte.build_xray_template).

Each LTE entry is a whitelisted RU IP. Routing must send RU domains/IPs -> `direct`
(egress the entry's own RU IP) and everything else -> the foreign relay outbound
(VLESS+Reality -> the existing foreign server). The foreign relay target is
parameterized via module constants so each LTE entry (bgg/lla/lff/lhh) can chain to
Frankfurt / LA / Helsinki.
"""

from __future__ import annotations

import os
import sys

import pytest

# Import the operator script from backend/scripts (it has no heavy deps at import).
_SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, _SCRIPTS)
import setup_lte as lte  # noqa: E402


def _rule_tags(template: dict) -> list[tuple]:
    return [(tuple(r.get("domain") or []), tuple(r.get("ip") or []),
             r.get("outboundTag")) for r in template["routing"]["rules"]]


def test_ru_traffic_goes_direct() -> None:
    t = lte.build_xray_template("relay-uuid-123")
    # RU TLDs and geoip:ru -> direct (egress this server's own RU IP).
    for rule in t["routing"]["rules"]:
        if "geoip:ru" in (rule.get("ip") or []):
            assert rule["outboundTag"] == "direct"
        if lte.RU_DOMAINS == (rule.get("domain") or []):
            assert rule["outboundTag"] == "direct"


def test_catch_all_goes_to_foreign_relay() -> None:
    t = lte.build_xray_template("relay-uuid-123")
    catch = [r for r in t["routing"]["rules"]
             if r.get("inboundTag") == [lte.INBOUND_TAG]
             and r.get("outboundTag") == lte.RELAY_OUTBOUND_TAG]
    assert catch, "missing catch-all inbound -> relay-to-foreign rule"
    assert catch[0].get("network") == "tcp,udp"


def test_domain_strategy_ipifnonmatch() -> None:
    t = lte.build_xray_template("relay-uuid-123")
    assert t["routing"]["domainStrategy"] == "IPIfNonMatch"


def test_relay_outbound_uses_parameterized_target() -> None:
    """With LA relay env, the outbound points at LA's host/pbk/sid/sni (not Frankfurt)."""
    # Override the module-level constants (read by build_xray_template at call time).
    orig = (lte.RELAY_HOST, lte.RELAY_PBK, lte.RELAY_SID, lte.RELAY_SNI, lte.RELAY_PORT)
    try:
        lte.RELAY_HOST = "216.227.169.120"
        lte.RELAY_PORT = 443
        lte.RELAY_PBK = "LA_PUBKEY_PLACEHOLDER"
        lte.RELAY_SID = "ladeadbeef"
        lte.RELAY_SNI = "lla.bravada-connect.online"
        t = lte.build_xray_template("relay-uuid-456")
    finally:
        (lte.RELAY_HOST, lte.RELAY_PBK, lte.RELAY_SID, lte.RELAY_SNI, lte.RELAY_PORT) = orig

    ob = next(o for o in t["outbounds"] if o["tag"] == lte.RELAY_OUTBOUND_TAG)
    assert ob["protocol"] == "vless"
    vnext = ob["settings"]["vnext"][0]
    assert vnext["address"] == "216.227.169.120"
    assert vnext["users"][0]["id"] == "relay-uuid-456"
    rs = ob["streamSettings"]["realitySettings"]
    assert rs["publicKey"] == "LA_PUBKEY_PLACEHOLDER"
    assert rs["shortId"] == "ladeadbeef"
    assert rs["serverName"] == "lla.bravada-connect.online"
    assert ob["streamSettings"]["network"] == "tcp"
    assert ob["streamSettings"]["security"] == "reality"


def test_relay_outbound_flow_empty() -> None:
    """The RELAY (outbound) user carries no flow — Vision is a client->entry property,
    not used on the entry->foreign relay hop."""
    t = lte.build_xray_template("relay-uuid-789")
    ob = next(o for o in t["outbounds"] if o["tag"] == lte.RELAY_OUTBOUND_TAG)
    assert ob["settings"]["vnext"][0]["users"][0]["flow"] == ""
