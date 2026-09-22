from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from scripts import configure_xray_auto_selection as auto


def _outbound(tag: str, *, host: str) -> dict:
    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": 443,
                    "users": [{"id": f"secret-{tag}", "encryption": "none"}],
                }
            ]
        },
        "streamSettings": {"network": "tcp", "security": "reality"},
    }


def _profile_data() -> dict:
    return {
        "name": "normal",
        "host": "192.0.2.10",
        "user": "operator",
        "key_path": "/secure/operator_key",
        "inbound_tags": ["auto-in"],
        "replace_outbound_tags": ["old-fixed-route"],
        "vless_route": 65001,
        "outbound_prefix": "auto-normal-",
        "balancer_tag": "balancer-normal",
        "fallback_tag": "auto-normal-de",
        "probe_url": "https://example.com/generate_204",
        "probe_interval": "30s",
        "outbounds": [
            _outbound("auto-normal-de", host="192.0.2.20"),
            _outbound("auto-normal-fi", host="192.0.2.30"),
        ],
        "publish": {
            "server_ids": [26],
            "manual_label": "Германия 1.0",
            "manual_country_flag": "🇩🇪",
            "manual_country_code": "DE",
            "auto_label": "Автовыбор",
            "auto_country_flag": "⚡",
            "auto_country_code": "",
            "surface": "both",
        },
    }


def _xray_config() -> dict:
    return {
        "inbounds": [
            {"tag": "api", "protocol": "dokodemo-door"},
            {"tag": "auto-in", "protocol": "vless", "settings": {"clients": []}},
        ],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            _outbound("auto-normal-stale", host="192.0.2.99"),
            {"tag": "unrelated-proxy", "protocol": "vless"},
        ],
        "routing": {
            "balancers": [{"tag": "other-balancer", "selector": ["unrelated-"]}],
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "domain": ["domain:ru"], "outboundTag": "direct"},
                {
                    "type": "field",
                    "domain": ["domain:foreign.example"],
                    "outboundTag": "old-fixed-route",
                },
                {
                    "type": "field",
                    "inboundTag": ["auto-in"],
                    "network": "tcp,udp",
                    "outboundTag": "old-fixed-route",
                },
                {"type": "field", "network": "tcp,udp", "outboundTag": "direct"},
            ],
        },
        "observatory": {
            "subjectSelector": ["unrelated-"],
            "probeUrl": "https://existing.example/probe",
            "probeInterval": "1m",
        },
    }


def test_parse_profile_accepts_normal_and_lte_contract() -> None:
    normal = auto.parse_profile(_profile_data())
    lte_data = _profile_data()
    lte_data.update(
        {
            "name": "lte",
            "outbound_prefix": "auto-lte-",
            "balancer_tag": "balancer-lte",
            "fallback_tag": "auto-lte-de",
            "outbounds": [
                _outbound("auto-lte-de", host="192.0.2.40"),
                _outbound("auto-lte-lt", host="192.0.2.50"),
            ],
            "publish": {
                "server_ids": [34],
                "manual_label": "Обход белых списков, Германия",
                "manual_country_flag": "🌐",
                "auto_label": "LTE Автовыбор",
                "auto_country_flag": "📶",
                "surface": "happ",
            },
        }
    )
    lte = auto.parse_profile(lte_data)

    assert normal.publish is not None and normal.publish.surface == "both"
    assert lte.publish is not None and lte.publish.surface == "happ"
    assert normal.vless_route == lte.vless_route == 65001
    assert len(normal.outbounds) == len(lte.outbounds) == 2


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data.update(outbounds=data["outbounds"][:1]), "минимум два"),
        (lambda data: data.update(fallback_tag="auto-normal-missing"), "fallback_tag"),
        (lambda data: data.update(inbound_tags=["api"]), "служебный inbound"),
        (lambda data: data.update(vless_route=0), "vless_route"),
        (lambda data: data["publish"].update(surface="unknown"), "publish.surface"),
        (
            lambda data: data["outbounds"][0].update(tag="foreign-prefix-de"),
            "тег outbound",
        ),
    ],
)
def test_parse_profile_rejects_unsafe_profiles(mutate, message: str) -> None:
    data = _profile_data()
    mutate(data)
    with pytest.raises(ValueError, match=message):
        auto.parse_profile(data)


def test_merge_replaces_only_owned_sections_and_is_idempotent() -> None:
    profile = auto.parse_profile(_profile_data())
    original = _xray_config()

    merged = auto.merge_auto_selection(original, profile)
    merged_twice = auto.merge_auto_selection(merged, profile)

    assert merged == merged_twice
    assert original == _xray_config()
    outbound_tags = [item["tag"] for item in merged["outbounds"]]
    assert outbound_tags == [
        "direct",
        "unrelated-proxy",
        "auto-normal-de",
        "auto-normal-fi",
    ]
    assert merged["routing"]["balancers"] == [
        {"tag": "other-balancer", "selector": ["unrelated-"]},
        {
            "tag": "balancer-normal",
            "selector": ["auto-normal-"],
            "fallbackTag": "auto-normal-de",
            "strategy": {"type": "leastPing"},
        },
    ]
    rules = merged["routing"]["rules"]
    auto_rule_index = next(i for i, rule in enumerate(rules) if rule.get("balancerTag") == "balancer-normal")
    global_fallback_index = next(
        i for i, rule in enumerate(rules) if rule.get("outboundTag") == "direct" and rule.get("network") == "tcp,udp"
    )
    assert auto_rule_index < global_fallback_index
    assert {
        "type": "field",
        "domain": ["domain:foreign.example"],
        "vlessRoute": "65001",
        "balancerTag": "balancer-normal",
    } in rules
    assert {
        "type": "field",
        "domain": ["domain:foreign.example"],
        "outboundTag": "old-fixed-route",
    } in rules
    assert {
        "type": "field",
        "inboundTag": ["auto-in"],
        "network": "tcp,udp",
        "outboundTag": "old-fixed-route",
    } in rules
    assert any(rule.get("vlessRoute") == "65001" and rule.get("balancerTag") == "balancer-normal" for rule in rules)
    assert {"type": "field", "domain": ["domain:ru"], "outboundTag": "direct"} in rules
    assert merged["observatory"] == {
        "subjectSelector": ["unrelated-", "auto-normal-"],
        "probeUrl": "https://existing.example/probe",
        "probeInterval": "1m",
        "enableConcurrency": True,
    }


def test_merge_requires_existing_inbound() -> None:
    profile = auto.parse_profile(_profile_data())
    config = _xray_config()
    config["inbounds"] = [{"tag": "api"}]
    with pytest.raises(ValueError, match="auto-in"):
        auto.merge_auto_selection(config, profile)


def test_merge_routes_only_alias_and_preserves_manual_inbound() -> None:
    profile = auto.parse_profile(_profile_data())
    config = _xray_config()
    config["inbounds"].append({"tag": "manual-in", "protocol": "vless"})
    config["routing"]["rules"] = [
        {
            "type": "field",
            "inboundTag": ["auto-in", "manual-in"],
            "domain": ["domain:foreign.example"],
            "outboundTag": "old-fixed-route",
        },
        {
            "type": "field",
            "inboundTag": ["auto-in", "manual-in"],
            "network": "tcp,udp",
            "outboundTag": "old-fixed-route",
        },
    ]

    rules = auto.merge_auto_selection(config, profile)["routing"]["rules"]

    assert {
        "type": "field",
        "inboundTag": ["auto-in", "manual-in"],
        "domain": ["domain:foreign.example"],
        "vlessRoute": "65001",
        "balancerTag": "balancer-normal",
    } in rules
    assert {
        "type": "field",
        "inboundTag": ["auto-in", "manual-in"],
        "domain": ["domain:foreign.example"],
        "outboundTag": "old-fixed-route",
    } in rules
    assert {
        "type": "field",
        "inboundTag": ["auto-in", "manual-in"],
        "network": "tcp,udp",
        "outboundTag": "old-fixed-route",
    } in rules
    assert any(rule.get("vlessRoute") == "65001" and rule.get("balancerTag") == "balancer-normal" for rule in rules)


def test_merge_removes_legacy_inbound_scoped_balancer_rules() -> None:
    profile = auto.parse_profile(_profile_data())
    config = _xray_config()
    config["routing"]["rules"].insert(
        1,
        {
            "type": "field",
            "inboundTag": ["auto-in"],
            "domain": ["domain:legacy.example"],
            "balancerTag": "balancer-normal",
        },
    )

    rules = auto.merge_auto_selection(config, profile)["routing"]["rules"]

    assert not any(rule.get("domain") == ["domain:legacy.example"] for rule in rules)
    assert all(rule.get("inboundTag") != ["auto-in"] for rule in rules if rule.get("balancerTag") == "balancer-normal")


def test_merge_rejects_vless_route_owned_by_another_rule() -> None:
    profile = auto.parse_profile(_profile_data())
    config = _xray_config()
    config["routing"]["rules"].insert(
        0,
        {"type": "field", "vlessRoute": "65001", "outboundTag": "unrelated-proxy"},
    )

    with pytest.raises(ValueError, match="уже используется"):
        auto.merge_auto_selection(config, profile)


def test_profile_summary_does_not_disclose_outbound_credentials() -> None:
    profile = auto.parse_profile(_profile_data())
    summary = auto.profile_summary(profile)
    assert "secret-auto" not in summary
    assert "192.0.2.20" not in summary
    assert "replace=old-fixed-route" in summary
    assert "route=65001" in summary
    assert "candidates=2" in summary


def test_load_profile_requires_private_file_permissions(tmp_path: Path) -> None:
    path = tmp_path / "auto.json"
    path.write_text(json.dumps({"profiles": [_profile_data()]}), encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        auto.load_profile(path, "normal")
    path.chmod(0o600)
    assert auto.load_profile(path, "normal").name == "normal"


class _RemoteFile:
    def __init__(self, files: dict[str, str], path: str) -> None:
        self._files = files
        self._path = path

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def write(self, value: str) -> None:
        self._files[self._path] = value


class _Sftp:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def open(self, path: str, _mode: str) -> _RemoteFile:
        return _RemoteFile(self.files, path)

    def close(self) -> None:
        return None


class _SshClient:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.closed = False

    def open_sftp(self) -> _Sftp:
        return _Sftp(self.files)

    def close(self) -> None:
        self.closed = True


def test_apply_does_not_restore_stale_backup_when_service_fails(monkeypatch) -> None:
    profile = auto.parse_profile(_profile_data())
    ssh = _SshClient()
    commands: list[str] = []
    active_checks = 0

    def fake_run(_cli, command: str, timeout: int = 30):
        nonlocal active_checks
        del timeout
        commands.append(command)
        if command.startswith("sudo cat"):
            return 0, json.dumps(_xray_config()), ""
        if command.startswith("systemctl is-active"):
            active_checks += 1
            return (1, "failed\n", "") if active_checks == 1 else (0, "active\n", "")
        return 0, "", ""

    monkeypatch.setattr(auto, "connect", lambda *_args: ssh)
    monkeypatch.setattr(auto, "_run", fake_run)
    monkeypatch.setattr(auto, "apply_remote_config", lambda *_args, **_kwargs: True)

    assert auto.apply_profile(profile, dry_run=False) is False
    assert ssh.closed is True
    assert not any("sudo cp" in cmd for cmd in commands)
    assert active_checks == 1


def test_apply_aborts_when_durable_writer_fails(monkeypatch) -> None:
    profile = auto.parse_profile(_profile_data())
    ssh = _SshClient()
    commands: list[str] = []

    def fake_run(_cli, command: str, timeout: int = 30):
        del timeout
        commands.append(command)
        return (0, json.dumps(_xray_config()), "") if command.startswith("sudo cat") else (0, "", "")

    monkeypatch.setattr(auto, "connect", lambda *_args: ssh)
    monkeypatch.setattr(auto, "_run", fake_run)
    monkeypatch.setattr(auto, "apply_remote_config", lambda *_args, **_kwargs: False)

    assert auto.apply_profile(profile, dry_run=False) is False
    assert not ssh.files
    assert not any("install -m" in cmd for cmd in commands)


def test_apply_reports_ssh_failure_without_traceback(monkeypatch, capsys) -> None:
    profile = auto.parse_profile(_profile_data())
    monkeypatch.setattr(auto, "connect", lambda *_args: (_ for _ in ()).throw(OSError("no route")))

    assert auto.apply_profile(profile, dry_run=True) is False
    assert "SSH-подключение" in capsys.readouterr().err


class _AsyncContext:
    def __init__(self, value=None) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args) -> None:
        return None


class _Connection:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.call = None

    def transaction(self) -> _AsyncContext:
        return _AsyncContext()

    async def fetch(self, query: str, *args):
        self.call = (query, args)
        return copy.deepcopy(self.rows)


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _AsyncContext:
        return _AsyncContext(self.connection)


@pytest.mark.asyncio
async def test_publish_updates_only_guarded_bare_xray_rows() -> None:
    profile = auto.parse_profile(_profile_data())
    connection = _Connection(
        [
            {
                "id": 26,
                "label": "Германия 1.0",
                "country_flag": "🇩🇪",
                "country_code": "DE",
                "client_surface": "both",
                "auto_selection_route": 65001,
                "auto_selection_label": "Автовыбор",
                "auto_selection_country_flag": "⚡",
                "auto_selection_country_code": "",
            }
        ]
    )
    rows = await auto.publish_profile(_Pool(connection), profile)

    assert rows[0]["label"] == "Германия 1.0"
    assert rows[0]["auto_selection_label"] == "Автовыбор"
    query, args = connection.call
    assert "is_active = TRUE" in query
    assert "provider_kind = 'bare_xray'" in query
    assert "split_part(link_template, '#', 1)" in query
    assert args[-2] == [26]
    assert args[-1] == "%F0%9F%87%A9%F0%9F%87%AA%20%D0%93%D0%B5%D1%80%D0%BC%D0%B0%D0%BD%D0%B8%D1%8F%201.0"


@pytest.mark.asyncio
async def test_publish_rolls_back_on_unexpected_row_count() -> None:
    profile = auto.parse_profile(_profile_data())
    with pytest.raises(RuntimeError, match="обновлено: 0"):
        await auto.publish_profile(_Pool(_Connection([])), profile)


def test_main_rejects_publish_without_apply(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        auto.main(["--config", "/secure/auto.json", "--profile", "normal", "--publish"])
    assert exc.value.code == 2
    assert "--publish разрешён только вместе с --apply" in capsys.readouterr().err
