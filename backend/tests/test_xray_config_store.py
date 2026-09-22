"""Отзыв сохраняется после restart, включая сбои и конкурентные операции."""

import asyncio
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.issuance import xray_config_store as store
from app.issuance.xray_api_client import XrayApiClient, alter_add_payload, alter_remove_payload


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "inbounds": [
                    {
                        "tag": "tcp",
                        "protocol": "vless",
                        "settings": {
                            "clients": [
                                {"email": "user-1", "id": "old", "flow": "xtls-rprx-vision", "level": 0},
                                {"email": "other", "id": "other-key"},
                            ]
                        },
                        "streamSettings": {"security": "reality"},
                    },
                    {"tag": "xhttp", "protocol": "vless", "settings": {"clients": []}},
                ],
                "outbounds": [{"tag": "keep", "protocol": "freedom"}],
            }
        )
    )
    path.chmod(0o640)
    monkeypatch.setattr(store.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=0, stdout="")))
    return path


def clients(path, tag="tcp"):
    return next(i["settings"]["clients"] for i in json.loads(path.read_text())["inbounds"] if i["tag"] == tag)


def remove_request():
    return {
        "action": "remove",
        "tag": "tcp",
        "email": "user-1",
        "api_port": 10085,
        "rpc": json.loads(alter_remove_payload(tag="tcp", email="user-1")),
    }


def add_request(tag="tcp", email="user-1", key="new"):
    flow = "xtls-rprx-vision" if tag == "tcp" else ""
    return {
        "action": "add",
        "tag": tag,
        "clients": [{"email": email, "id": key, "flow": flow}],
        "api_port": 10085,
        "rpc": json.loads(alter_add_payload(tag=tag, email=email, uuid=key, flow=flow)),
    }


def test_rotation_survives_restart_without_reconcile(config_path, monkeypatch):
    before = json.loads(config_path.read_text())
    seen = []

    def rpc(method, payload, port):
        seen.append(clients(config_path))
        return True, ""

    monkeypatch.setattr(store, "_rpc", rpc)
    assert store.transaction(remove_request(), path=config_path)
    assert not any(c["id"] == "old" for c in seen[-1])
    assert store.transaction(add_request(), path=config_path)
    # Перезапуск читает исключительно файл, backend/reconcile не участвуют.
    restarted = {c["email"]: c["id"] for c in clients(config_path)}
    assert restarted == {"other": "other-key", "user-1": "new"}
    after = json.loads(config_path.read_text())
    assert after["outbounds"] == before["outbounds"]
    assert after["inbounds"][1] == before["inbounds"][1]
    assert after["inbounds"][0]["streamSettings"] == before["inbounds"][0]["streamSettings"]
    assert config_path.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("fault", ["validation", "replace", "fsync"])
def test_persistence_failure_prevents_rpc(config_path, monkeypatch, fault):
    before = config_path.read_bytes()
    rpc = Mock(return_value=(True, ""))
    monkeypatch.setattr(store, "_rpc", rpc)
    if fault == "validation":
        store.subprocess.run.side_effect = subprocess.CalledProcessError(1, "xray")
    else:
        monkeypatch.setattr(store.os, fault, Mock(side_effect=OSError("disk failure")))
    with pytest.raises((OSError, subprocess.SubprocessError)):
        store.transaction(remove_request(), path=config_path)
    assert config_path.read_bytes() == before
    rpc.assert_not_called()
    assert not list(config_path.parent.glob(".bravada-clients-*.json"))


def test_rpc_failure_never_restores_revoked_key(config_path, monkeypatch):
    monkeypatch.setattr(store, "_rpc", Mock(return_value=(False, "")))
    assert not store.transaction(remove_request(), path=config_path)
    assert not any(c["id"] == "old" for c in clients(config_path))
    monkeypatch.setattr(store, "_rpc", Mock(return_value=(True, "")))
    assert store.transaction(remove_request(), path=config_path)
    assert not any(c["id"] == "old" for c in clients(config_path))


def test_snapshot_removes_disk_only_keys_without_rpc(config_path, monkeypatch):
    rpc = Mock()
    monkeypatch.setattr(store, "_rpc", rpc)
    request = {
        "action": "snapshot",
        "tag": "tcp",
        "clients": [{"email": "user-1", "id": "new", "flow": "xtls-rprx-vision"}],
    }
    assert store.transaction(request, path=config_path)
    assert clients(config_path) == [{"email": "user-1", "id": "new", "flow": "xtls-rprx-vision", "level": 0}]
    first_mtime = config_path.stat().st_mtime_ns
    assert store.transaction(request, path=config_path)
    assert config_path.stat().st_mtime_ns == first_mtime
    rpc.assert_not_called()


def test_concurrent_inbounds_preserve_both_updates(config_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def rpc(method, payload, port):
        if payload["tag"] == "tcp":
            entered.set()
            assert release.wait(3)
        return True, ""

    monkeypatch.setattr(store, "_rpc", rpc)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(store.transaction, add_request(), path=config_path)
        assert entered.wait(2)
        second = pool.submit(store.transaction, add_request("xhttp", "x-user-1", "x-new"), path=config_path)
        assert not second.done()
        release.set()
        assert first.result(3) and second.result(3)
    assert any(c["id"] == "new" for c in clients(config_path))
    assert clients(config_path, "xhttp") == [{"email": "x-user-1", "id": "x-new", "flow": ""}]


def test_operator_update_and_rollback_preserve_current_keys(config_path, monkeypatch):
    stale = json.loads(config_path.read_text())
    stale["log"] = {"loglevel": "warning"}
    monkeypatch.setattr(store, "_rpc", Mock(return_value=(True, "")))
    assert store.transaction(remove_request(), path=config_path)
    assert store.transaction(add_request(), path=config_path)
    assert store.transaction({"action": "replace_config", "config": stale}, path=config_path)
    assert not any(c["id"] == "old" for c in clients(config_path))
    current = config_path.read_bytes()

    def run(argv, **kwargs):
        if argv[0] == "/usr/bin/systemctl" and kwargs.get("check"):
            raise subprocess.CalledProcessError(1, "systemctl")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(store.subprocess, "run", run)
    assert not store.transaction({"action": "replace_config", "config": stale}, path=config_path)
    assert config_path.read_bytes() == current


def test_rejects_wrong_inbound_without_writing(config_path):
    before = config_path.read_bytes()
    request = remove_request()
    request["tag"] = "nonexistent"
    with pytest.raises(ValueError):
        store.transaction(request, path=config_path)
    assert config_path.read_bytes() == before


def test_api_routes_add_remove_through_durable_transaction(monkeypatch):
    calls = []
    client = object.__new__(XrayApiClient)
    client._config = SimpleNamespace(xray_inbound_tag="tcp", transport_type="tcp")
    client._api_port = 10085
    client._run_durable_mutation = lambda request: (calls.append(request) is None, "")
    assert asyncio.run(client.remove_client(email="user-1"))
    assert asyncio.run(client.add_client(user_uuid="new", email="user-1"))
    assert [c["action"] for c in calls] == ["remove", "add"]
    assert calls[1]["clients"] == [{"email": "user-1", "id": "new", "flow": "xtls-rprx-vision"}]


def test_retry_repeats_fsync_after_replace_failure(config_path, monkeypatch):
    original = store.os.fsync
    count = 0

    def fail_directory(fd):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("directory fsync failed")
        return original(fd)

    rpc = Mock(return_value=(True, ""))
    monkeypatch.setattr(store, "_rpc", rpc)
    monkeypatch.setattr(store.os, "fsync", fail_directory)
    with pytest.raises(OSError):
        store.transaction(remove_request(), path=config_path)
    rpc.assert_not_called()
    assert not any(c["id"] == "old" for c in clients(config_path))
    assert store.transaction(remove_request(), path=config_path)
    assert count == 4
    rpc.assert_called_once()


@pytest.mark.parametrize("exit_code", [-1, None])
def test_uncertain_ssh_result_quarantines_before_return(monkeypatch, exit_code):
    channel = SimpleNamespace(
        exit_status_ready=lambda: True, recv_exit_status=lambda: exit_code, shutdown_write=lambda: None
    )
    stream = Mock(channel=channel)
    ssh = Mock()
    if exit_code is None:
        ssh.exec_command.side_effect = OSError("connection lost")
    else:
        ssh.exec_command.return_value = (stream, stream, stream)
    quarantined = []
    monkeypatch.setattr(store, "_quarantine_remote", lambda target: quarantined.append(target))
    if exit_code is None:
        with pytest.raises(OSError):
            store.run_remote_transaction(ssh, remove_request(), root=True)
    else:
        assert not store.run_remote_transaction(ssh, remove_request(), root=True)
    assert quarantined == [ssh]


def test_confirmed_ssh_exit_needs_no_quarantine(monkeypatch):
    channel = SimpleNamespace(exit_status_ready=lambda: True, recv_exit_status=lambda: 0, shutdown_write=lambda: None)
    stream = Mock(channel=channel)
    stream.read.return_value = b"ok\n"
    ssh = Mock()
    ssh.exec_command.return_value = (stream, stream, stream)
    quarantine = Mock()
    monkeypatch.setattr(store, "_quarantine_remote", quarantine)
    assert store.run_remote_transaction(ssh, remove_request(), root=True)
    quarantine.assert_not_called()
    command = ssh.exec_command.call_args.args[0]
    assert "--kill-after=5s 100s" in command
    assert '"email": "user-1"' not in command


def test_uncertain_delayed_start_waits_full_ttl_after_disconnect(monkeypatch):
    elapsed = [0.0]
    waits = []
    ssh = Mock()

    def delayed(*args, **kwargs):
        elapsed[0] = 20.0
        raise OSError("delayed SSH failure")

    ssh.exec_command.side_effect = delayed
    monkeypatch.setattr(store.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(store.time, "sleep", lambda seconds: waits.append((elapsed[0], seconds)))
    with pytest.raises(OSError):
        store.run_remote_transaction(ssh, remove_request(), root=True)
    ssh.close.assert_called_once()
    assert waits == [(20.0, 110)]
