"""Tests for cascade_apply_banks.merge_banks_direct — the pure, idempotent merge that
inserts the Russian bank/payment domains -> direct (RU egress) routing rule on
cascade-entries."""

from __future__ import annotations

import copy
import json

from scripts.cascade_apply_banks import BANK_DOMAINS_SET, merge_banks_direct


def _cfg(rules: list) -> dict:
    return {"routing": {"rules": copy.deepcopy(rules)}, "outbounds": [{"tag": "direct", "protocol": "freedom"}]}


def _is_banks_rule(r: dict) -> bool:
    return r.get("outboundTag") == "direct" and set(r.get("domain") or []) == BANK_DOMAINS_SET


def test_banks_rule_inserted_after_api_and_private() -> None:
    cfg = _cfg(
        [
            {"inboundTag": ["api"], "outboundTag": "api"},
            {"ip": ["geoip:private"], "outboundTag": "direct"},
            {"domain": ["domain:tiktok.com"], "outboundTag": "albania-relay"},
            {"domain": ["regexp:.*\\.ru$"], "outboundTag": "direct"},
        ]
    )

    out = merge_banks_direct(cfg)
    rules = out["routing"]["rules"]

    # banks rule at index 2 — right after api (0) + private (1), before tiktok (.com) and .ru
    assert _is_banks_rule(rules[2])
    assert rules[0]["outboundTag"] == "api"
    assert "geoip:private" in rules[1]["ip"]
    assert rules[3]["domain"] == ["domain:tiktok.com"]
    assert rules[4]["domain"] == ["regexp:.*\\.ru$"]


def test_merge_is_idempotent() -> None:
    cfg = _cfg(
        [
            {"inboundTag": ["api"], "outboundTag": "api"},
            {"ip": ["geoip:private"], "outboundTag": "direct"},
            {"domain": ["domain:sberbank.com"], "outboundTag": "albania-relay"},
        ]
    )

    once = merge_banks_direct(cfg)
    twice = merge_banks_direct(once)

    assert once == twice  # second merge changes nothing
    banks_rules = [r for r in twice["routing"]["rules"] if _is_banks_rule(r)]
    assert len(banks_rules) == 1  # exactly one banks rule, no duplicates


def test_merge_preserves_all_existing_rules() -> None:
    original_rules = [
        {"inboundTag": ["api"], "outboundTag": "api"},
        {"ip": ["geoip:private"], "outboundTag": "direct"},
        {"domain": ["domain:vk.com"], "outboundTag": "direct"},
        {"domain": ["domain:tiktok.com"], "outboundTag": "albania-relay"},
        {"domain": ["regexp:.*\\.ru$"], "outboundTag": "direct"},
        {"ip": ["geoip:ru"], "outboundTag": "direct"},
        {"inboundTag": ["yt-tcp"], "outboundTag": "albania-relay"},
    ]
    cfg = _cfg(original_rules)

    out = merge_banks_direct(cfg)
    out_rules = out["routing"]["rules"]

    assert len(out_rules) == len(original_rules) + 1  # originals + 1 banks rule
    for r in original_rules:
        assert r in out_rules, f"missing original rule: {r}"


def test_merge_does_not_mutate_input() -> None:
    cfg = _cfg([{"ip": ["geoip:private"], "outboundTag": "direct"}, {"domain": ["domain:x.com"], "outboundTag": "y"}])
    snapshot = json.loads(json.dumps(cfg))

    merge_banks_direct(cfg)

    assert cfg == snapshot  # input untouched
