"""Tests for bare_xray_provision + xui_to_bare_xray pure helpers (no SSH).

SSH-фазы (survey/prep/cutover/db-flip/e2e/health, phase-a/b/c, rollback) —
операторский инструментарий по образцу test_canonicalize_routing: покрыты
pure-функции (бандл/флип-SQL/патч клиентов/e2e-конфиг/adapt x-ui рендера)."""

from __future__ import annotations

import json

import pytest
from scripts.bare_xray_provision import (
    BUNDLE_EXCLUDES,
    BUNDLE_PATHS,
    DEFAULT_FW_PORTS,
    XRAY_SERVICE_UNIT,
    bundle_command,
    e2e_test_config,
    flip_sql,
    patch_config_clients,
    ufw_script,
)
from scripts.canonicalize_routing import CONFIG_PATH
from scripts.enable_bare_xray_stats import stats_enabled
from scripts.xui_to_bare_xray import adapt_xui_config

# --- bare_xray_provision: bundle ----------------------------------------------


def test_bundle_command_always_excludes_authorized_keys() -> None:
    cmd = bundle_command()
    # gotcha #1 US-миграции: tar-бандл перезатирал authorized_keys нового бокса
    for exclude in BUNDLE_EXCLUDES:
        assert f"--exclude={exclude}" in cmd
    assert "authorized_keys" in BUNDLE_EXCLUDES[0]
    for path in BUNDLE_PATHS:
        assert path in cmd
    assert cmd.startswith("sudo tar czf /tmp/mig_bundle.tgz")
    assert " -C / " in cmd


def test_xray_service_unit_etalon() -> None:
    # эталон юнита: asset-env (без него xray не видит geoip) + restart-policy
    assert "Environment=XRAY_LOCATION_ASSET=/usr/local/share/xray" in XRAY_SERVICE_UNIT
    assert "ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json" in XRAY_SERVICE_UNIT
    assert "Restart=on-failure" in XRAY_SERVICE_UNIT
    assert "WantedBy=multi-user.target" in XRAY_SERVICE_UNIT


def test_ufw_script_puts_ssh_port_first() -> None:
    lines = ufw_script(DEFAULT_FW_PORTS).splitlines()
    # 22 первой — не закрываем себе дверь при --force enable
    assert lines[0] == "sudo ufw allow 22/tcp"
    assert "sudo ufw --force enable" in lines


# --- bare_xray_provision: db-flip SQL ------------------------------------------


def test_flip_sql_builds_update_for_ids() -> None:
    sql = flip_sql([8, 9], "176.109.107.226")
    assert sql == (
        "UPDATE vpn_servers SET server_host='176.109.107.226' WHERE id IN (8,9) RETURNING id, label, server_host;"
    )


def test_flip_sql_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        flip_sql([], "1.2.3.4")  # пустой список
    with pytest.raises(ValueError):
        flip_sql([0], "1.2.3.4")  # неположительный id
    with pytest.raises(ValueError):
        flip_sql([8], "1.2.3.4'; DROP TABLE vpn_servers;--")  # инъекция
    with pytest.raises(ValueError):
        flip_sql([8], "bad host!")


# --- bare_xray_provision: cutover patch + e2e -----------------------------------


def _config_with_inbounds() -> dict:
    return {
        "inbounds": [
            {"tag": "api", "protocol": "dokodemo-door", "listen": "127.0.0.1", "port": 10085},
            {"tag": "in-443-tcp", "protocol": "vless", "settings": {"clients": [{"email": "stale"}]}},
            {"tag": "in-8443-xhttp", "protocol": "vless", "settings": {}},
        ],
        "outbounds": [{"tag": "to-exit", "protocol": "vless"}],
    }


def test_patch_config_clients_replaces_only_matched_tags() -> None:
    live = {"in-443-tcp": [{"email": "user-a", "id": "u1", "flow": "xtls-rprx-vision"}]}
    patched = patch_config_clients(_config_with_inbounds(), live)
    assert patched["inbounds"][1]["settings"]["clients"] == live["in-443-tcp"]
    # прочие инбаунды не тронуты
    assert patched["inbounds"][2]["settings"] == {}
    # вход не мутируется
    assert _config_with_inbounds()["inbounds"][1]["settings"]["clients"] == [{"email": "stale"}]


def test_e2e_test_config_shape() -> None:
    cfg = e2e_test_config(uuid="UUID", pbk="PBK", sid="SID", sni="SNI", host="1.2.3.4")
    assert cfg["inbounds"][0]["protocol"] == "socks"
    ob = cfg["outbounds"][0]
    vnext = ob["settings"]["vnext"][0]
    assert vnext["address"] == "1.2.3.4" and vnext["port"] == 443
    assert vnext["users"][0]["id"] == "UUID"
    rs = ob["streamSettings"]["realitySettings"]
    assert rs["publicKey"] == "PBK" and rs["shortId"] == "SID" and rs["serverName"] == "SNI"


# --- xui_to_bare_xray: adapt_xui_config ------------------------------------------


def _xui_render() -> dict:
    """Типовой рендер 3x-ui (FQCN-сервисы, dokodemo api-инбаунд + routing-правило)."""
    return {
        "log": {"loglevel": "warning"},
        "api": {
            "services": ["xray.app.proxyman.command.HandlerService", "xray.app.log.command.LoggerService"],
            "tag": "api",
        },
        "inbounds": [
            {
                "tag": "api",
                "listen": "127.0.0.1",
                "port": 62789,
                "protocol": "dokodemo-door",
                "settings": {"address": "127.0.0.1"},
            },
            {
                "tag": "relay-in",
                "protocol": "vless",
                "port": 443,
                "settings": {"clients": [{"email": "relay-from-clr-helsinki", "id": "uuid-1"}]},
                "streamSettings": {"realitySettings": {"dest": "127.0.0.1:10443"}},
            },
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"},
            ]
        },
    }


def test_adapt_converts_api_to_fleet_etalon() -> None:
    adapted, notes = adapt_xui_config(_xui_render())
    api = adapted["api"]
    assert api["listen"] == "127.0.0.1:10085"
    # FQCN-стиль сохранён, Reflection/Stats добавлены той же формой
    assert "xray.app.reflection.command.ReflectionService" in api["services"]
    assert "xray.app.stats.command.StatsService" in api["services"]
    assert "xray.app.proxyman.command.HandlerService" in api["services"]
    # dokodemo api-инбаунд и его routing-правило убраны
    assert all(ib.get("tag") != "api" for ib in adapted["inbounds"])
    assert all(r.get("inboundTag") != ["api"] for r in adapted["routing"]["rules"])
    # stats включён (контракт adapt)
    assert adapted["stats"] == {}
    assert adapted["policy"]["levels"]["0"]["statsUserUplink"] is True
    assert adapted["policy"]["levels"]["0"]["statsUserDownlink"] is True


def test_adapt_preserves_relay_payload_byte_equal() -> None:
    """Содержательное (clients/keypair/reality) остаётся байт-в-байт — меняется
    только способ запуска."""
    src = _xui_render()
    adapted, _ = adapt_xui_config(src)
    relay_src = src["inbounds"][1]
    relay_out = next(ib for ib in adapted["inbounds"] if ib.get("tag") == "relay-in")
    assert relay_out == relay_src
    assert adapted["outbounds"] == src["outbounds"]
    assert adapted["log"] == src["log"]


def test_adapt_idempotent() -> None:
    once, _ = adapt_xui_config(_xui_render())
    twice, notes2 = adapt_xui_config(once)
    assert twice == once
    assert notes2 == []


def test_adapt_short_style_services_stay_short() -> None:
    src = _xui_render()
    src["api"]["services"] = ["HandlerService"]
    adapted, _ = adapt_xui_config(src)
    assert adapted["api"]["services"] == ["HandlerService", "ReflectionService", "StatsService"]


def test_adapt_from_config_without_api_block() -> None:
    src = _xui_render()
    del src["api"]
    src["inbounds"] = [ib for ib in src["inbounds"] if ib.get("tag") != "api"]
    adapted, notes = adapt_xui_config(src)
    assert adapted["api"]["listen"] == "127.0.0.1:10085"
    assert any(n == "+api" for n in notes)
    assert stats_enabled(adapted)


def test_adapt_does_not_mutate_input() -> None:
    src = _xui_render()
    snapshot = json.dumps(src, sort_keys=True)
    adapt_xui_config(src)
    assert json.dumps(src, sort_keys=True) == snapshot
    # конфиг-путь конвертера совпадает с эталонным fleet-путём
    assert CONFIG_PATH == "/usr/local/etc/xray/config.json"
