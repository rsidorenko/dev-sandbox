"""Unit tests for mirror_cloudru_clients operator script (no DB/SSH).

Loads the script via importlib (same pattern as test_rebuild_panel_clients) and covers
the pure desired-state helpers. paramiko is never imported (lazy in _ssh_apply).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _BACKEND_DIR / "scripts" / "mirror_cloudru_clients.py"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mirror_cloudru_clients", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load_script_module()


# ---------------------------------------------------------------------------
# desired_client_uuids
# ---------------------------------------------------------------------------


def test_desired_client_uuids_deterministic_dedup_sorted(script: ModuleType) -> None:
    ids = ["u111", "u222", "u111", "u333"]
    out1 = script.desired_client_uuids(ids)
    out2 = script.desired_client_uuids(ids)

    assert out1 == out2                      # deterministic
    assert out1 == sorted(out1)              # sorted
    assert len(out1) == 3                    # deduped (u111 once)
    # matches the per-transport tcp uuid mirror
    assert out1[0] == script._vless_uuid_for_transport("u111", "tcp")


def test_desired_client_uuids_empty(script: ModuleType) -> None:
    assert script.desired_client_uuids([]) == []


# ---------------------------------------------------------------------------
# build_desired_clients
# ---------------------------------------------------------------------------


def test_build_desired_clients_shape(script: ModuleType) -> None:
    uuids = ["aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"]
    clients = script.build_desired_clients(uuids)
    assert clients == [
        {"id": uuids[0], "flow": "xtls-rprx-vision"},
        {"id": uuids[1], "flow": "xtls-rprx-vision"},
    ]


# ---------------------------------------------------------------------------
# rewrite_clients
# ---------------------------------------------------------------------------


def _sample_config(clients: list[dict] | None = None) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "port": 443,
                "protocol": "vless",
                "settings": {
                    "decryption": "none",
                    "clients": list(clients or []),
                },
                "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"shortIds": ["a4084380"]}},
            }
        ],
        "outbounds": [{"tag": "frankfurt-relay", "protocol": "vless"}],
        "routing": {"domainStrategy": "IPIfNonMatch"},
    }


def test_rewrite_clients_sets_and_flags_change(script: ModuleType) -> None:
    config = _sample_config(clients=[{"id": "stale", "flow": "xtls-rprx-vision"}])
    desired = [{"id": "new", "flow": "xtls-rprx-vision"}]
    new, changed = script.rewrite_clients(config, desired)

    assert changed is True
    assert new["inbounds"][0]["settings"]["clients"] == desired
    # other settings preserved
    assert new["inbounds"][0]["settings"]["decryption"] == "none"
    # rest of config untouched
    assert new["outbounds"][0]["tag"] == "frankfurt-relay"
    assert new["routing"]["domainStrategy"] == "IPIfNonMatch"
    # original not mutated
    assert config["inbounds"][0]["settings"]["clients"] != desired


def test_rewrite_clients_idempotent_when_identical(script: ModuleType) -> None:
    desired = [{"id": "x", "flow": "xtls-rprx-vision"}]
    config = _sample_config(clients=desired)
    _, changed = script.rewrite_clients(config, desired)
    assert changed is False


def test_rewrite_clients_no_inbounds_is_noop(script: ModuleType) -> None:
    new, changed = script.rewrite_clients({"outbounds": []}, [{"id": "x", "flow": "xtls-rprx-vision"}])
    assert changed is False
    assert new == {"outbounds": []}


def test_rewrite_clients_settings_as_json_string(script: ModuleType) -> None:
    desired = [{"id": "x", "flow": "xtls-rprx-vision"}]
    config = {
        "inbounds": [
            {"port": 443, "settings": json.dumps({"decryption": "none", "clients": []})}
        ]
    }
    new, changed = script.rewrite_clients(config, desired)
    assert changed is True
    assert new["inbounds"][0]["settings"]["clients"] == desired
    assert new["inbounds"][0]["settings"]["decryption"] == "none"
