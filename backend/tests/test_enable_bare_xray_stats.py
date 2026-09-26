"""Tests for enable_bare_xray_stats pure helpers (no SSH/grpcurl/paramiko).

SSH-часть скрипта (apply_to_target) — операторский инструментарий, как у
canonicalize_routing: покрыты только pure-функции merge/predicate/commands
и контракт реестра TARGETS (актуальные хосты, explicit_only CDN-ноги)."""

from __future__ import annotations

import json

import pytest
from scripts.canonicalize_routing import CONFIG_PATH
from scripts.enable_bare_xray_stats import (
    STATS_SERVICE_NAME,
    TARGETS,
    configured_user_counts,
    get_inbound_users_command,
    merge_stats_keys,
    query_stats_command,
    stats_enabled,
    vless_inbound_tags,
)


def _base_config() -> dict:
    """Минимальный bare_xray-конфиг: api-инбаунд + api.services с HandlerService
    и один vless-инбаунд с клиентами — как на реальных боксах флота."""
    return {
        "inbounds": [
            {"tag": "api", "protocol": "dokodemo-door", "listen": "127.0.0.1", "port": 10085},
            {
                "tag": "cloudru-tcp",
                "protocol": "vless",
                "settings": {"clients": [{"email": "user-abc", "id": "uuid-1"}]},
            },
            {
                "tag": "cloudru-xhttp",
                "protocol": "vless",
                "settings": {"clients": [{"email": "x-user-abc", "id": "uuid-2"}]},
            },
        ],
        "api": {"services": ["xray.app.proxyman.command.HandlerService"]},
    }


# --- merge_stats_keys ----------------------------------------------------------


def test_merge_adds_all_three_keys() -> None:
    merged, changes = merge_stats_keys(_base_config())
    assert merged["stats"] == {}
    level0 = merged["policy"]["levels"]["0"]
    assert level0["statsUserUplink"] is True
    assert level0["statsUserDownlink"] is True
    assert merged["api"]["services"] == [
        "xray.app.proxyman.command.HandlerService",
        STATS_SERVICE_NAME,
    ]
    assert len(changes) == 4  # stats + 2 флага level-0 + services


def test_merge_idempotent_second_pass_no_changes() -> None:
    merged, _ = merge_stats_keys(_base_config())
    merged2, changes2 = merge_stats_keys(merged)
    assert merged2 == merged
    assert changes2 == []
    assert stats_enabled(merged2)


def test_merge_matches_short_name_style_of_fleet_configs() -> None:
    # bare_xray-флот (setup_relay.py) пишет короткие имена — StatsService должен
    # добавляться той же формой, без смешения стилей в одном списке.
    cfg = _base_config()
    cfg["api"]["services"] = ["HandlerService", "ReflectionService"]
    merged, changes = merge_stats_keys(cfg)
    assert merged["api"]["services"] == ["HandlerService", "ReflectionService", "StatsService"]
    assert "+api.services+=StatsService" in changes
    assert stats_enabled(merged)
    # повторный прогон — no-op
    _, changes2 = merge_stats_keys(merged)
    assert changes2 == []


def test_merge_preserves_existing_level0_siblings_and_services() -> None:
    cfg = _base_config()
    cfg["policy"] = {"levels": {"0": {"statsUserUplink": True, "handshake": 4}}}
    cfg["api"]["services"] = ["xray.app.proxyman.command.HandlerService", "some.Other"]
    merged, changes = merge_stats_keys(cfg)
    level0 = merged["policy"]["levels"]["0"]
    assert level0["handshake"] == 4  # соседние флаги уровня не затёрты
    assert merged["api"]["services"] == [
        "xray.app.proxyman.command.HandlerService",
        "some.Other",
        STATS_SERVICE_NAME,  # аппенд, существующие сервисы на месте
    ]
    assert "+policy.levels.0.statsUserDownlink=true" in changes
    assert "+policy.levels.0.statsUserUplink=true" not in changes  # уже был


def test_merge_does_not_touch_nonempty_stats_block() -> None:
    cfg = _base_config()
    cfg["stats"] = {"anything": True}
    merged, changes = merge_stats_keys(cfg)
    assert merged["stats"] == {"anything": True}
    assert '+"stats": {}' not in changes


def test_merge_normalizes_null_stats_to_empty_dict() -> None:
    cfg = _base_config()
    cfg["stats"] = None
    merged, changes = merge_stats_keys(cfg)
    assert merged["stats"] == {}
    assert stats_enabled(merged)
    assert '+"stats": {}' in changes


def test_merge_does_not_mutate_input() -> None:
    cfg = _base_config()
    snapshot = json.dumps(cfg, sort_keys=True)
    merge_stats_keys(cfg)
    assert json.dumps(cfg, sort_keys=True) == snapshot


def test_merge_raises_without_api_services() -> None:
    cfg = _base_config()
    del cfg["api"]
    with pytest.raises(ValueError, match="api.services"):
        merge_stats_keys(cfg)
    cfg2 = _base_config()
    cfg2["api"] = {"services": "not-a-list"}
    with pytest.raises(ValueError, match="api.services"):
        merge_stats_keys(cfg2)


# --- stats_enabled predicate -----------------------------------------------------


def test_stats_enabled_false_on_base_config() -> None:
    assert stats_enabled(_base_config()) is False


def test_stats_enabled_requires_all_three_keys() -> None:
    merged, _ = merge_stats_keys(_base_config())
    assert stats_enabled(merged) is True
    # убираем по одному — предикат должен гаснуть
    no_stats = json.loads(json.dumps(merged))
    del no_stats["stats"]
    assert stats_enabled(no_stats) is False
    no_flag = json.loads(json.dumps(merged))
    del no_flag["policy"]["levels"]["0"]["statsUserDownlink"]
    assert stats_enabled(no_flag) is False
    no_service = json.loads(json.dumps(merged))
    no_service["api"]["services"].remove(STATS_SERVICE_NAME)
    assert stats_enabled(no_service) is False
    # короткая форма (стиль флота) тоже валидна как «включено»
    short_style = json.loads(json.dumps(merged))
    short_style["api"]["services"] = ["HandlerService", "ReflectionService", "StatsService"]
    assert stats_enabled(short_style) is True


# --- inbound/user-count helpers ---------------------------------------------------


def test_vless_inbound_tags_skips_api_inbound() -> None:
    assert vless_inbound_tags(_base_config()) == ["cloudru-tcp", "cloudru-xhttp"]


def test_configured_user_counts_per_inbound() -> None:
    assert configured_user_counts(_base_config()) == {"cloudru-tcp": 1, "cloudru-xhttp": 1}
    empty = _base_config()
    empty["inbounds"][1]["settings"] = {}
    assert configured_user_counts(empty)["cloudru-tcp"] == 0


# --- grpcurl command shapes --------------------------------------------------------


def test_query_stats_command_single_encoding() -> None:
    cmd = query_stats_command()
    # req!r = один уровень JSON-кодирования (двойной json.dumps ломает grpcurl)
    assert cmd.startswith("sudo grpcurl -plaintext -d ")
    assert """'{"pattern": ["user>>>"], "reset": false}'""" in cmd
    assert cmd.endswith(f"localhost:10085 {STATS_SERVICE_NAME}/QueryStats")


def test_get_inbound_users_command_shape() -> None:
    cmd = get_inbound_users_command("cloudru-tcp")
    assert """'{"tag": "cloudru-tcp"}'""" in cmd
    assert cmd.endswith("xray.app.proxyman.command.HandlerService/GetInboundUsers")


# --- TARGETS registry contract ------------------------------------------------------


def test_targets_unique_names_and_current_hosts() -> None:
    names = [t.name for t in TARGETS]
    assert len(names) == len(set(names))
    hosts = {t.name: t.host for t in TARGETS}
    # актуальные хосты флота (fleet-state 2026-09). ⚠️ YT из canonicalize_routing
    # (158.160.82.113) удалён 2026-08-26 — сюда намеренно НЕ копировался.
    assert hosts["YT"] == "93.77.165.48"
    assert hosts["DE"] == "158.160.188.35"
    assert hosts["FI"] == "176.108.244.24"
    assert hosts["LT"] == "45.91.52.204"
    assert hosts["US"] == "176.109.107.226"
    assert hosts["CDN-LT"] == "89.169.178.45"
    assert hosts["CDN-DE"] == "93.77.160.88"
    assert "158.160.82.113" not in hosts.values()


def test_targets_cdn_legs_are_explicit_only() -> None:
    explicit = {t.name for t in TARGETS if t.explicit_only}
    assert explicit == {"CDN-LT", "CDN-DE"}
    # у каждого таргета задан путь к ключу и конфиг по каноническому пути
    for t in TARGETS:
        assert t.key, f"{t.name} without ssh key"
        assert t.config == CONFIG_PATH
